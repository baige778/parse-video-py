# -*- coding: utf-8 -*-
"""抖音持久化登录态常驻浏览器单例（方案A）

使用磁盘持久化的 Chromium user_data_dir profile，让浏览器长期持有并滚动刷新
抖音登录态（sessionid / ttwid / UIFID 等设备指纹），替代已失效（"Uifid Not Found"）
的 Cookie 文件直连路径。解析与扫码登录共用同一 profile：

- 解析：在常驻 context 上开新 page 打开视频页，捕获 /aweme/v1/web/aweme/detail/ 接口。
- 登录：在常驻 context 上打开登录页截取二维码，扫码成功后登录态自动落盘，
  后续解析直接复用，无需再导出 Cookie。

环境变量：
  DOUYIN_DATA_DIR                         数据目录（默认 /data，回退 ./data、系统临时目录）
  PARSE_VIDEO_PY_DOUYIN_PROFILE_DIR       浏览器 profile 目录（默认 <data>/browser_profile）
  PARSE_VIDEO_PY_DOUYIN_BROWSER_CHANNEL   浏览器 channel（默认内置 Chromium）
  PARSE_VIDEO_PY_DOUYIN_BROWSER_GOTO_TIMEOUT   页面加载超时 ms（默认 10000）
  PARSE_VIDEO_PY_DOUYIN_BROWSER_POLL_TIMEOUT   接口轮询超时 ms（默认 8000）
  PARSE_VIDEO_PY_DOUYIN_BROWSER_REUSE_TTL      常驻生命周期秒（默认 600）
  PARSE_VIDEO_PY_DOUYIN_BROWSER_REUSE_MAX      复用次数上限（默认 100）
  PARSE_VIDEO_PY_DOUYIN_BROWSER_LOCK_TIMEOUT   锁等待超时秒（默认 20）
  PARSE_VIDEO_PY_DOUYIN_BROWSER_HEADED_RETRY   无头失败是否转有头（默认 0，容器关闭）
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

_WIN_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)

# 登录态判定 Cookie
_LOGIN_COOKIE_KEYS = ("sessionid", "sessionid_ss")

# 反自动化检测补丁：抖音登录/解析页会加载 uc-secure-tool-detect 无感验证脚本，
# 无头浏览器若不伪装（navigator.platform 暴露 Linux 与 Windows UA 矛盾、
# navigator.webdriver 为 true 等），风控会拦截 get_qrcode 等接口，导致二维码不生成。
_STEALTH_SCRIPT = """
(() => {
  const spoof = (prop, getter) => {
    try { Object.defineProperty(navigator, prop, { get: getter }); } catch (e) {}
  };
  spoof('webdriver', () => undefined);
  spoof('platform', () => 'Win32');
  spoof('languages', () => ['zh-CN', 'zh', 'en-US', 'en']);
  spoof('plugins', () => [1, 2, 3, 4, 5]);
  spoof('hardwareConcurrency', () => 8);
  spoof('deviceMemory', () => 8);
  try {
    window.chrome = window.chrome || { runtime: {}, loadTimes: function () {}, csi: function () {}, app: {} };
  } catch (e) {}
})();
"""


def _env_int(name: str, default: int) -> int:
    """从环境变量读取整数配置，解析失败时回退默认值。"""
    try:
        return int((os.environ.get(name) or "").strip() or default)
    except (TypeError, ValueError):
        return default


def _log(tag: str, msg: str) -> None:
    """抖音浏览器结构化日志（stderr）。"""
    print(f"[parse-video-py][douyin-browser][{tag}] {msg}", file=sys.stderr, flush=True)


# ---- 浏览器配置项 ----
_GOTO_TIMEOUT = _env_int("PARSE_VIDEO_PY_DOUYIN_BROWSER_GOTO_TIMEOUT", 10000)
_POLL_TIMEOUT = _env_int("PARSE_VIDEO_PY_DOUYIN_BROWSER_POLL_TIMEOUT", 8000)
_CHANNEL_ENV = (os.environ.get("PARSE_VIDEO_PY_DOUYIN_BROWSER_CHANNEL") or "").strip()
_REUSE_TTL = _env_int("PARSE_VIDEO_PY_DOUYIN_BROWSER_REUSE_TTL", 600)
_REUSE_MAX = _env_int("PARSE_VIDEO_PY_DOUYIN_BROWSER_REUSE_MAX", 100)
_LOCK_TIMEOUT = _env_int("PARSE_VIDEO_PY_DOUYIN_BROWSER_LOCK_TIMEOUT", 20)
HEADED_RETRY = _env_int("PARSE_VIDEO_PY_DOUYIN_BROWSER_HEADED_RETRY", 0)

# 低配内存环境（路由器/容器）下更稳的 Chromium 启动参数
_BROWSER_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--no-first-run",
    "--mute-audio",
]


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


def resolve_profile_dir() -> Path:
    """解析浏览器 profile 目录：默认 <data>/browser_profile。"""
    env_dir = os.environ.get("PARSE_VIDEO_PY_DOUYIN_PROFILE_DIR")
    p = Path(env_dir) if env_dir else (_DATA_DIR / "browser_profile")
    p.mkdir(parents=True, exist_ok=True)
    return p


_DATA_DIR = resolve_data_dir()
DATA_DIR = _DATA_DIR
PROFILE_DIR = resolve_profile_dir()


# ---- 常驻浏览器状态（进程内单例，lazy 启动 + 定期回收） ----
_lock = asyncio.Lock()
_pw = None
_context = None
_channel = None
_launched_at = 0.0
_use_count = 0


async def _acquire(timeout: float) -> bool:
    """带超时获取浏览器锁；超时返回 False。"""
    try:
        await asyncio.wait_for(_lock.acquire(), timeout=timeout)
        return True
    except (asyncio.TimeoutError, asyncio.CancelledError):
        return False


def _candidate_channels() -> list:
    """浏览器 channel 探测顺序：上次成功 channel 优先，内置 Chromium 兜底。"""
    order: list = []
    if _CHANNEL_ENV:
        order.append(_CHANNEL_ENV)
    elif _channel is not None:
        order.append(_channel)
    order.append(None)
    if not _CHANNEL_ENV:
        order.extend(["msedge", "chrome"])
    seen: set = set()
    out: list = []
    for c in order:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


async def _recycle_locked() -> None:
    """关闭常驻 context（保留磁盘 profile，登录态持久化）。"""
    global _context, _launched_at, _use_count
    if _context is not None:
        try:
            await _context.close()
        except Exception:
            pass
        _context = None
    _launched_at = 0.0
    _use_count = 0
    _log("browser", "常驻浏览器已回收")


async def _force_recycle() -> None:
    """无锁强制回收：用于锁超时后判断浏览器卡死的情况。"""
    await _recycle_locked()


async def _get_context_locked():
    """懒加载持久化 Chromium context（headless），超生命周期/次数自动重建。

    调用方需已持有 _lock。
    """
    global _pw, _context, _channel, _launched_at, _use_count
    from playwright.async_api import async_playwright

    now = time.monotonic()
    need_rebuild = (
        _context is None
        or (now - _launched_at) > _REUSE_TTL
        or _use_count >= _REUSE_MAX
    )
    if need_rebuild:
        await _recycle_locked()
        if _pw is None:
            _pw = await async_playwright().start()
        last_error = "无法启动任何可用浏览器"
        for channel in _candidate_channels():
            try:
                launch_kwargs = {
                    "headless": True,
                    "args": list(_BROWSER_ARGS),
                }
                if channel:
                    launch_kwargs["channel"] = channel
                ctx = await _pw.chromium.launch_persistent_context(
                    user_data_dir=str(PROFILE_DIR),
                    locale="zh-CN",
                    viewport={"width": 1280, "height": 800},
                    user_agent=_WIN_UA,
                    **launch_kwargs,
                )
                await ctx.add_init_script(_STEALTH_SCRIPT)
            except Exception as exc:
                last_error = f"启动 {channel or '内置Chromium'} 失败：{exc}"
                continue
            _context = ctx
            _channel = channel
            _launched_at = now
            _use_count = 0
            _log(
                "browser",
                f"常驻浏览器已启动 channel={channel or '内置Chromium'} profile={PROFILE_DIR}",
            )
            break
        if _context is None:
            raise RuntimeError(last_error)
    _use_count += 1
    return _context


async def with_context(fn: Callable):
    """在浏览器锁内运行 fn(context)，返回 fn 的结果。

    锁等待超时（浏览器卡死）时强制回收并抛出异常，避免死锁拖垮后续请求。
    """
    if not await _acquire(_LOCK_TIMEOUT):
        _log(
            "browser",
            f"等待浏览器锁超过 {_LOCK_TIMEOUT}s，判定常驻浏览器卡死，强制回收",
        )
        await _force_recycle()
        raise RuntimeError("常驻浏览器卡死，已回收，请重试")
    try:
        ctx = await _get_context_locked()
        return await fn(ctx)
    finally:
        _lock.release()


async def _extract_from_context(context, detail_url: str) -> dict:
    """在给定 context 上开新 page，捕获视频详情接口数据；返回 item 或抛异常。"""
    captured: dict = {}

    # 预热：先访问首页，让 sec_sdk 完成初始化并刷新 ttwid/msToken 等风控 Cookie；
    # 否则直接打开视频页时 detail 接口会被风控静默拦截（HTTP 200 + 空 body）。
    warmup_page = await context.new_page()
    try:
        await warmup_page.goto(
            "https://www.douyin.com/",
            wait_until="domcontentloaded",
            timeout=_GOTO_TIMEOUT,
        )
        await warmup_page.wait_for_timeout(4000)
    except Exception:
        pass
    finally:
        try:
            await warmup_page.close()
        except Exception:
            pass

    page = await context.new_page()

    async def on_response(resp) -> None:
        if "/aweme/v1/web/aweme/detail/" in resp.url and resp.status == 200:
            try:
                data = await resp.json()
            except Exception:
                return
            if isinstance(data, dict) and data.get("aweme_detail"):
                captured["item"] = data["aweme_detail"]

    try:
        page.on("response", on_response)
        await page.goto(
            detail_url,
            wait_until="domcontentloaded",
            timeout=_GOTO_TIMEOUT,
        )
        deadline = time.monotonic() + _POLL_TIMEOUT / 1000.0
        while "item" not in captured and time.monotonic() < deadline:
            await page.wait_for_timeout(500)
        if "item" in captured:
            return captured["item"]
        # 页面已打开但未捕获到详情接口：区分「登录态失效」与「风控/验证码」，
        # 给上层（插件）一个可识别的失效信号，便于提醒用户重新扫码登录。
        cookies = await context.cookies()
        names = {c.get("name") for c in cookies if isinstance(c, dict)}
        if not (set(_LOGIN_COOKIE_KEYS) & names):
            raise RuntimeError("抖音登录态已失效，请重新扫描二维码登录")
        raise RuntimeError("页面已打开，但未捕获到视频详情接口（可能触发验证码）")
    finally:
        try:
            await page.close()
        except Exception:
            pass


async def extract_item(video_id: str) -> dict:
    """用持久化浏览器解析抖音视频详情，返回 aweme_detail item。"""
    if not video_id:
        raise ValueError("抖音视频 ID 为空")
    detail_url = f"https://www.douyin.com/video/{video_id}"
    return await with_context(lambda ctx: _extract_from_context(ctx, detail_url))


async def extract_item_headed(video_id: str) -> dict:
    """有头模式解析（独立浏览器，容器/无显示环境会失败，仅在显式开启时用）。"""
    from playwright.async_api import async_playwright

    detail_url = f"https://www.douyin.com/video/{video_id}"
    pw = await async_playwright().start()
    last_error = "无法启动任何可用浏览器"
    try:
        for channel in _candidate_channels():
            try:
                launch_kwargs = {
                    "headless": False,
                    "args": list(_BROWSER_ARGS),
                }
                if channel:
                    launch_kwargs["channel"] = channel
                ctx = await pw.chromium.launch_persistent_context(
                    user_data_dir=str(PROFILE_DIR),
                    locale="zh-CN",
                    viewport={"width": 1280, "height": 800},
                    user_agent=_WIN_UA,
                    **launch_kwargs,
                )
            except Exception as exc:
                last_error = f"启动 {channel or '内置Chromium'} 失败：{exc}"
                continue
            try:
                return await _extract_from_context(ctx, detail_url)
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            finally:
                try:
                    await ctx.close()
                except Exception:
                    pass
        raise RuntimeError(last_error)
    finally:
        try:
            await pw.stop()
        except Exception:
            pass


async def get_cookies() -> Optional[list]:
    """读取常驻浏览器当前 Cookie（供登录轮询判断登录态）。"""
    try:
        return await with_context(lambda ctx: ctx.cookies())
    except Exception:
        return None


async def is_logged_in() -> bool:
    """判断常驻浏览器 profile 当前是否已登录抖音（首页 DOM 判定）。

    profile/self 接口在无头 + 风控环境下并不可靠：即使实际已登录，接口也可能
    返回 status_code=8「用户未登录」，造成「已登录被误判为未登录」。故改为直接
    校验首页 DOM 的登录区：
      已登录 -> 页面右上角出现「退出登录」菜单项（未登录时不会渲染）；
      未登录 -> 页面右上角存在可见的「登录」入口。
    最后回退到 sessionid cookie 是否存在。
    """
    async def _check(ctx):
        page = await ctx.new_page()
        try:
            # 预热首页，等待右上角登录区完成渲染
            try:
                await page.goto(
                    "https://www.douyin.com/",
                    wait_until="domcontentloaded",
                    timeout=_GOTO_TIMEOUT,
                )
                await page.wait_for_timeout(4000)
            except Exception:
                pass

            # 已登录：DOM 文本里出现「退出登录」（用 textContent 而非 innerText，
            # 因为该菜单项默认处于折叠状态，innerText 读不到）。
            try:
                text = await page.evaluate(
                    "() => document.body ? document.body.textContent : ''"
                )
            except Exception:
                text = ""
            if text and "退出登录" in text:
                return True

            # 未登录：右上角存在可见的「登录」入口。
            for sel in (
                "button:has-text('登录')",
                "a:has-text('登录')",
                "span:has-text('登录')",
            ):
                try:
                    loc = page.locator(sel).first
                    if await loc.count() > 0 and await loc.is_visible(timeout=1000):
                        return False
                except Exception:
                    continue

            # 兜底：存在 sessionid 即视为已登录。
            try:
                cookies = await ctx.cookies()
            except Exception:
                cookies = []
            names = {c.get("name") for c in cookies if isinstance(c, dict)}
            return bool(set(_LOGIN_COOKIE_KEYS) & names)
        except Exception:
            return False
        finally:
            try:
                await page.close()
            except Exception:
                pass

    try:
        return await with_context(_check)
    except Exception:
        return False


async def recycle() -> None:
    """主动回收常驻浏览器（保留 profile 登录态）。"""
    if not await _acquire(_LOCK_TIMEOUT):
        await _force_recycle()
        return
    try:
        await _recycle_locked()
    finally:
        _lock.release()


async def shutdown() -> None:
    """应用退出时调用：关闭 context 并停止 Playwright。"""
    global _pw
    if await _acquire(_LOCK_TIMEOUT):
        try:
            await _recycle_locked()
        finally:
            _lock.release()
    else:
        await _force_recycle()
    if _pw is not None:
        try:
            await _pw.stop()
        except Exception:
            pass
        _pw = None