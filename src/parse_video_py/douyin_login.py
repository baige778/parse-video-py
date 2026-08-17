# -*- coding: utf-8 -*-
"""抖音网页端扫码登录管理器（Playwright 异步版）

能力：
1. 启动无头 Chromium 打开抖音网页版，截取登录二维码；
2. 后台轮询登录状态，登录成功后把 Cookie 导出到文件；
3. 浏览器用完即关，及时释放内存。

产物文件（默认 /data，可用 DOUYIN_DATA_DIR 覆盖）：
  douyin_qrcode.png   登录二维码图片
  douyin_cookies.txt  Netscape 格式 Cookie（兼容 douyin_fallback.load_douyin_cookies）

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
import tempfile
import time
from pathlib import Path
from typing import Optional

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
# 2025-08-17 实测：抖音登录弹窗中二维码是 div[class*=login] 下的第二个 img，src 为 base64 data URI
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

_WIN_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)


def resolve_data_dir() -> Path:
    """解析数据目录：优先 DOUYIN_DATA_DIR，其次 /data，再 ./data，最后系统临时目录。"""
    candidates: list[str] = []
    env_dir = os.environ.get("DOUYIN_DATA_DIR")
    if env_dir:
        candidates.append(env_dir)
    candidates.extend(["/data", "./data"])
    for raw in candidates:
        if not raw:
            continue
        p = Path(raw)
        try:
            p.mkdir(parents=True, exist_ok=True)
            if os.access(p, os.W_OK):
                return p
        except OSError:
            continue
    return Path(tempfile.gettempdir()) / "douyin"


_DATA_DIR = resolve_data_dir()
QRCODE_PATH = _DATA_DIR / "douyin_qrcode.png"
COOKIES_PATH = _DATA_DIR / "douyin_cookies.txt"


class DouyinLoginManager:
    """单例式登录管理器：同一时间只允许一个登录会话。"""

    def __init__(self) -> None:
        self._pw = None
        self._browser = None
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

            await self._close_browser_locked()
            self._state = "failed"
            self._last_error = ""
            try:
                await self._ensure_playwright_locked()
                self._browser = await self._pw.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                    ],
                )
                self._context = await self._browser.new_context(
                    locale="zh-CN",
                    viewport={"width": 1280, "height": 800},
                    user_agent=_WIN_UA,
                )
                self._page = await self._context.new_page()

                await self._open_login_qr()
                qr_element = await self._locate_qr_element()
                if qr_element is None:
                    raise RuntimeError("未能定位到登录二维码元素（页面结构可能已变化）")

                buf = await qr_element.screenshot()
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
                await self._close_browser_locked()
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
        """取消当前登录会话，关闭浏览器。"""
        async with self._lock:
            if self._state == "pending":
                self._state = "cancelled"
            await self._close_browser_locked()
            return {
                "code": 200,
                "msg": "已取消登录",
                "data": {"status": self._state},
            }

    async def shutdown(self) -> None:
        """应用退出时调用：关闭浏览器并停止 Playwright。"""
        async with self._lock:
            await self._close_browser_locked()
            if self._pw is not None:
                try:
                    await self._pw.stop()
                except Exception:
                    pass
                self._pw = None

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
        if self._last_error:
            data["error"] = self._last_error
        msg = "暂无进行中的登录" if self._state == "idle" else "ok"
        return {"code": 200, "msg": msg, "data": data}

    async def _ensure_playwright_locked(self) -> None:
        if self._pw is None:
            # 惰性导入：仅在真正启动登录时才要求安装 playwright
            from playwright.async_api import async_playwright

            self._pw = await async_playwright().start()

    async def _open_login_qr(self) -> None:
        page = self._page
        await page.goto(_LOGIN_URL, wait_until="domcontentloaded", timeout=45000)
        # 抖音首页可能自动弹出登录框，等待渲染
        await page.wait_for_timeout(2500)

        # 若二维码已直接出现，直接返回
        if await self._locate_qr_element() is not None:
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
            if await self._locate_qr_element() is not None:
                return
            await page.wait_for_timeout(500)

    async def _locate_qr_element(self):
        """在主页及各 iframe 中寻找二维码元素。"""
        frames = [self._page]
        try:
            frames += [f for f in self._page.frames]
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
        """后台轮询：登录成功导出 Cookie，失效/超时则关闭浏览器。"""
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
                    await self._close_browser_locked()

    async def _poll_once_locked(self) -> None:
        """在持有锁的情况下轮询一次登录状态。"""
        try:
            cookies = await self._context.cookies()
        except Exception as exc:
            self._state = "failed"
            self._last_error = f"{type(exc).__name__}: {exc}"
            await self._close_browser_locked()
            return

        if self._has_login_cookie(cookies):
            self._save_cookies(cookies)
            self._state = "success"
            await self._close_browser_locked()
            return

        if await self._is_qr_expired():
            self._state = "expired"
            await self._close_browser_locked()
            return

        if time.monotonic() - self._started_at >= self.timeout:
            self._state = "expired"
            self._last_error = "登录超时"
            await self._close_browser_locked()
            return

    def _has_login_cookie(self, cookies) -> bool:
        names = {c.get("name") for c in cookies if isinstance(c, dict)}
        return any(k in names for k in _LOGIN_COOKIE_KEYS)

    async def _is_qr_expired(self) -> bool:
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

    async def _close_browser_locked(self) -> None:
        # 取消后台轮询任务（避免取消当前任务本身）
        if self._watch_task is not None:
            current = asyncio.current_task()
            if self._watch_task is not current and not self._watch_task.done():
                self._watch_task.cancel()
            self._watch_task = None

        for closer in (self._close_page, self._close_context, self._close_browser):
            await closer()

    async def _close_page(self) -> None:
        if self._page is not None:
            try:
                await self._page.close()
            except Exception:
                pass
            self._page = None

    async def _close_context(self) -> None:
        if self._context is not None:
            try:
                await self._context.close()
            except Exception:
                pass
            self._context = None

    async def _close_browser(self) -> None:
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None


_manager: Optional[DouyinLoginManager] = None


def get_douyin_login_manager() -> DouyinLoginManager:
    global _manager
    if _manager is None:
        _manager = DouyinLoginManager()
    return _manager
