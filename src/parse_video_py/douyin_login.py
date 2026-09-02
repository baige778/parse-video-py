# -*- coding: utf-8 -*-
"""抖音网页端扫码登录管理器（复用磁盘持久化常驻浏览器）

复用 douyin_browser 提供的磁盘持久化 user_data_dir profile：
1. 在常驻 context 上打开抖音网页版，截取登录二维码；
2. 后台轮询登录状态，扫码成功后登录态自动持久化到浏览器 profile，
   后续解析直接复用，无需导出 Cookie。

产物文件（默认 /data，可用 DOUYIN_DATA_DIR 覆盖）：
  douyin_qrcode.png   登录二维码图片
  douyin_cookies.txt  登录成功后导出的 Cookie（仅作调试/备用兼容）

环境变量：
  DOUYIN_DATA_DIR        数据目录（默认 /data，不可写时回退到 ./data 或系统临时目录）
  DOUYIN_LOGIN_TIMEOUT   登录超时秒数（默认 300）

说明：
  抖音前端结构会不定期变化，登录按钮与二维码的定位采用多路选择器兜底；
  若线上结构变更导致定位失败，`_LOGIN_TRIGGER_SELECTORS` / `_QR_SELECTORS`
  是需要优先调整的位置。
"""

from __future__ import annotations

import asyncio
import base64
import os
import time
from typing import Optional

from . import douyin_browser

_LOGIN_URL = "https://www.douyin.com/"

# 登录成功的判定：浏览器里出现这些 Cookie 即认为扫码登录完成
_LOGIN_COOKIE_KEYS = ("sessionid", "sessionid_ss")

# 判定二维码失效的关键词（出现在页面文案里）
_QR_EXPIRED_KEYWORDS = (
    "二维码已失效",
    "二维码失效",
    "二维码过期",
    "已失效",
    "请刷新",
    "刷新二维码",
)

# 点击「登录」按钮的候选选择器（按优先级）
_LOGIN_TRIGGER_SELECTORS = (
    "button:has-text('登录')",
    "a:has-text('登录')",
    "span:has-text('登录')",
)

# 二维码元素候选选择器（按优先级，宽高需 >= _QR_MIN_SIZE 才算命中）
_QR_SELECTORS = (
    "div[class*='login'] img",  # 抖音登录弹窗二维码（优先）
    "img[src*='qrcode']",
    "img[src*='qr_code']",
    "img[src*='qr-code']",
    "canvas",
    "div[class*='qrcode']",
    "div[class*='qr-code']",
    "div[class*='login-qr']",
    "div[class*='qrCode']",
)
_QR_MIN_SIZE = 120

# 仅保留抖音域下的 Cookie，避免把第三方 Cookie 一并写入文件
_DOUYIN_COOKIE_DOMAINS = ("douyin.com", "iesdouyin.com")

_DEFAULT_TIMEOUT = 300
_POLL_INTERVAL = 2.0


QRCODE_PATH = douyin_browser.DATA_DIR / "douyin_qrcode.png"
COOKIES_PATH = douyin_browser.DATA_DIR / "douyin_cookies.txt"


class DouyinLoginManager:
    """单例式登录管理器：同一时间只允许一个登录会话。"""

    def __init__(self) -> None:
        self._context = None
        self._page = None
        self._state = "idle"  # idle / pending / success / expired / cancelled / failed
        self._started_at = 0.0
        self._last_error = ""
        self._lock = asyncio.Lock()
        self._watch_task: Optional[asyncio.Task] = None

    @property
    def timeout(self) -> float:
        try:
            return float(os.environ.get("DOUYIN_LOGIN_TIMEOUT", _DEFAULT_TIMEOUT))
        except ValueError:
            return float(_DEFAULT_TIMEOUT)

    # ---------------- 对外 API ----------------

    async def start_qrcode(self) -> dict:
        """启动一次扫码登录，返回二维码图片信息。"""
        async with self._lock:
            if self._state == "pending":
                return self._pending_payload("登录已在进行中")

            await self._reset_locked()
            self._state = "failed"
            self._last_error = ""
            try:
                async def body(ctx):
                    self._context = ctx
                    page = await ctx.new_page()
                    self._page = page
                    await self._open_login_qr(page)
                    qr_element = await self._locate_qr_element(page)
                    if qr_element is None:
                        raise RuntimeError("未能定位到登录二维码元素（页面结构可能已变化）")
                    return await qr_element.screenshot()

                buf = await douyin_browser.with_context(body)
                QRCODE_PATH.write_bytes(buf)

                self._state = "pending"
                self._started_at = time.monotonic()
                self._watch_task = asyncio.create_task(self._watch_loop())

                return {
                    "code": 200,
                    "msg": "ok",
                    "data": {
                        "status": "pending",
                        "qrcode_path": str(QRCODE_PATH),
                        "qrcode_base64": base64.b64encode(buf).decode("ascii"),
                        "expires_in": int(self.timeout),
                    },
                }
            except Exception as exc:
                self._state = "failed"
                self._last_error = f"{type(exc).__name__}: {exc}"
                await self._reset_locked()
                return {
                    "code": 500,
                    "msg": self._last_error,
                    "data": {"status": "failed"},
                }

    async def status(self) -> dict:
        """查询当前登录状态。"""
        async with self._lock:
            return self._status_payload_locked()

    async def cancel(self) -> dict:
        """取消当前登录会话，关闭登录页。"""
        async with self._lock:
            if self._state == "pending":
                self._state = "cancelled"
            await self._reset_locked()
            return {
                "code": 200,
                "msg": "已取消登录",
                "data": {"status": self._state},
            }

    async def shutdown(self) -> None:
        """应用退出时调用：关闭登录页并停止常驻浏览器。"""
        async with self._lock:
            await self._reset_locked()
        await douyin_browser.shutdown()

    # ---------------- 内部实现 ----------------

    def _pending_payload(self, msg: str) -> dict:
        return {
            "code": 200,
            "msg": msg,
            "data": {
                "status": "pending",
                "qrcode_path": str(QRCODE_PATH),
                "qrcode_base64": self._read_qrcode_base64(),
                "expires_in": int(self.timeout),
            },
        }

    def _status_payload_locked(self) -> dict:
        elapsed = int(time.monotonic() - self._started_at) if self._started_at else 0
        data = {"status": self._state, "elapsed": elapsed}
        if self._state == "success":
            data["cookie_path"] = str(COOKIES_PATH)
            data["profile_dir"] = str(douyin_browser.PROFILE_DIR)
        if self._last_error:
            data["error"] = self._last_error
        msg = "暂无进行中的登录" if self._state == "idle" else "ok"
        return {"code": 200, "msg": msg, "data": data}

    async def _open_login_qr(self, page) -> None:
        await page.goto(_LOGIN_URL, wait_until="domcontentloaded", timeout=45000)
        # 抖音首页可能自动弹出登录框，等待渲染
        await page.wait_for_timeout(2500)

        # 若二维码已直接出现，直接返回
        if await self._locate_qr_element(page) is not None:
            return

        # 尝试点击「登录」按钮
        for selector in _LOGIN_TRIGGER_SELECTORS:
            try:
                loc = page.locator(selector).first
                if await loc.count() == 0:
                    continue
                if await loc.is_visible(timeout=1500):
                    await loc.click(timeout=3000)
                    break
            except Exception:
                continue

        # 等待二维码出现（最多 10s）
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if await self._locate_qr_element(page) is not None:
                return
            await page.wait_for_timeout(500)

    async def _locate_qr_element(self, page):
        """在主页及各 iframe 中寻找二维码元素。"""
        frames = [page]
        try:
            frames += [f for f in page.frames]
        except Exception:
            pass

        for frame in frames:
            for selector in _QR_SELECTORS:
                try:
                    loc = frame.locator(selector)
                    count = await loc.count()
                except Exception:
                    continue
                for i in range(count):
                    try:
                        el = loc.nth(i)
                        if not await el.is_visible():
                            continue
                        box = await el.bounding_box()
                        if box and box["width"] >= _QR_MIN_SIZE and box["height"] >= _QR_MIN_SIZE:
                            return el
                    except Exception:
                        continue
        return None

    async def _watch_loop(self) -> None:
        """后台轮询：登录成功即结束，失效/超时则关闭登录页。"""
        try:
            while True:
                await asyncio.sleep(_POLL_INTERVAL)
                async with self._lock:
                    if self._state != "pending":
                        return
                    await self._poll_once_locked()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            async with self._lock:
                if self._state == "pending":
                    self._state = "failed"
                    self._last_error = f"{type(exc).__name__}: {exc}"

    async def _poll_once_locked(self) -> None:
        """在持有登录锁与浏览器锁的情况下轮询一次登录状态。"""
        async def body(ctx):
            cookies = await ctx.cookies()
            if self._has_login_cookie(cookies):
                self._save_cookies(cookies)
                self._state = "success"
                await self._close_page_locked()
                return

            if await self._is_qr_expired():
                self._state = "expired"
                self._last_error = "二维码已失效"
                await self._close_page_locked()
                return

            if time.monotonic() - self._started_at >= self.timeout:
                self._state = "expired"
                self._last_error = "登录超时"
                await self._close_page_locked()
                return

        try:
            await douyin_browser.with_context(body)
        except Exception as exc:
            self._state = "failed"
            self._last_error = f"{type(exc).__name__}: {exc}"
            await self._close_page_locked()

    def _has_login_cookie(self, cookies) -> bool:
        names = {c.get("name") for c in cookies if isinstance(c, dict)}
        return any(k in names for k in _LOGIN_COOKIE_KEYS)

    async def _is_qr_expired(self) -> bool:
        if self._page is None:
            return False
        try:
            text = await self._page.inner_text("body")
        except Exception:
            return False
        return any(kw in text for kw in _QR_EXPIRED_KEYWORDS)

    def _save_cookies(self, cookies) -> None:
        douyin_cookies = [c for c in cookies if self._is_douyin_cookie(c)]
        content = self._cookies_to_netscape(douyin_cookies)
        COOKIES_PATH.write_text(content, encoding="utf-8")

    @staticmethod
    def _is_douyin_cookie(c) -> bool:
        domain = (c.get("domain") or "").lstrip(".")
        return any(
            domain == d or domain.endswith("." + d) for d in _DOUYIN_COOKIE_DOMAINS
        )

    @staticmethod
    def _cookies_to_netscape(cookies) -> str:
        lines = [
            "# Netscape HTTP Cookie File",
            "# generated by parse-video-py douyin login",
        ]
        for c in cookies:
            if not isinstance(c, dict):
                continue
            domain = c.get("domain") or ""
            name = c.get("name") or ""
            if not domain or not name:
                continue
            include_sub = "TRUE" if domain.startswith(".") else "FALSE"
            path = c.get("path") or "/"
            secure = "TRUE" if c.get("secure") else "FALSE"
            expires = c.get("expires")
            expiry = int(expires) if isinstance(expires, (int, float)) and expires > 0 else 0
            value = c.get("value") or ""
            lines.append(
                f"{domain}\t{include_sub}\t{path}\t{secure}\t{expiry}\t{name}\t{value}"
            )
        return "\n".join(lines) + "\n"

    def _read_qrcode_base64(self) -> str:
        try:
            return base64.b64encode(QRCODE_PATH.read_bytes()).decode("ascii")
        except OSError:
            return ""

    async def _reset_locked(self) -> None:
        # 取消后台轮询任务（避免取消当前任务本身）
        if self._watch_task is not None:
            current = asyncio.current_task()
            if self._watch_task is not current and not self._watch_task.done():
                self._watch_task.cancel()
            self._watch_task = None
        await self._close_page_locked()

    async def _close_page_locked(self) -> None:
        if self._page is not None:
            try:
                await self._page.close()
            except Exception:
                pass
            self._page = None


_manager: Optional[DouyinLoginManager] = None


def get_douyin_login_manager() -> DouyinLoginManager:
    global _manager
    if _manager is None:
        _manager = DouyinLoginManager()
    return _manager