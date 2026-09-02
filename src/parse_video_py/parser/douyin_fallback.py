# -*- coding: utf-8 -*-
"""
抖音网页端风控兜底解析

抖音 web 端已启用 JS 风控（a_bogus / __ac_signature / 浏览器指纹），
原生分享页 HTML 解析会失效。本模块提供两条兜底路径：

1. 登录 Cookie 纯 HTTP：有 douyin.com 登录 Cookie 时直接调用
   /aweme/v1/web/aweme/detail/ 接口，登录态下无需 a_bogus 签名。
2. 无头浏览器：用 Playwright 驱动本机 Edge/Chrome 打开 PC 视频页，
   让页面 JS 自行完成风控，再从接口响应中提取数据。

Cookie 文件查找顺序：
   环境变量 PARSE_VIDEO_PY_DOUYIN_COOKIES
   -> 当前目录 douyin_cookies.txt / douyin_cookies.json

环境变量：
   PARSE_VIDEO_PY_DOUYIN_NO_BROWSER=1  禁用浏览器兜底（只走 Cookie 直连）
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

import httpx

from .base import ImgInfo, VideoAuthor, VideoInfo


_DOUYIN_MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
    "Mobile/15E148 Safari/604.1"
)
_EDGE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36 Edg/151.0.0.0"
)


def _env_int(name: str, default: int) -> int:
    """从环境变量读取整数配置，解析失败时回退默认值。"""
    try:
        return int((os.environ.get(name) or "").strip() or default)
    except (TypeError, ValueError):
        return default


def _log(tag: str, msg: str) -> None:
    """抖音解析结构化日志（stderr），带路径标记与耗时，便于排查。"""
    print(f"[parse-video-py][douyin][{tag}] {msg}", file=sys.stderr, flush=True)


# ---- 抖音解析可配置项（环境变量；默认值针对容器/常见场景） ----
_COOKIE_TIMEOUT = _env_int("PARSE_VIDEO_PY_DOUYIN_COOKIE_TIMEOUT", 5)
_COOKIE_RETRIES = _env_int("PARSE_VIDEO_PY_DOUYIN_COOKIE_RETRIES", 1)
_BROWSER_GOTO_TIMEOUT = _env_int("PARSE_VIDEO_PY_DOUYIN_BROWSER_GOTO_TIMEOUT", 15000)
_BROWSER_POLL_TIMEOUT = _env_int("PARSE_VIDEO_PY_DOUYIN_BROWSER_POLL_TIMEOUT", 10000)
_BROWSER_CHANNEL = (os.environ.get("PARSE_VIDEO_PY_DOUYIN_BROWSER_CHANNEL") or "").strip()
# 浏览器常驻复用：headless 模式复用单例，避免每请求冷启动；0 关闭。
_BROWSER_REUSE = _env_int("PARSE_VIDEO_PY_DOUYIN_BROWSER_REUSE", 1)
_BROWSER_REUSE_TTL = _env_int("PARSE_VIDEO_PY_DOUYIN_BROWSER_REUSE_TTL", 600)
_BROWSER_REUSE_MAX = _env_int("PARSE_VIDEO_PY_DOUYIN_BROWSER_REUSE_MAX", 100)


def _first_non_webp(url_list) -> str:
    for url in url_list or []:
        if url and not str(url).endswith(".webp"):
            return url
    return (url_list or [""])[0] or ""


def _parse_cookie_string(text: str) -> dict:
    """解析形如 'k=v; k2=v2' 的 Cookie 字符串。"""
    text = text.strip().lstrip("\ufeff")
    if text.lower().startswith("cookie"):
        text = "\n".join(text.splitlines()[1:])
    text = " ".join(line.strip() for line in text.splitlines() if line.strip())
    out = {}
    for part in text.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = k.strip()
        if k:
            out[k] = v.strip()
    return out


def _find_cookies_file() -> Optional[Path]:
    env_path = os.environ.get("PARSE_VIDEO_PY_DOUYIN_COOKIES")
    if env_path and Path(env_path).is_file():
        return Path(env_path)
    for name in ("douyin_cookies.txt", "douyin_cookies.json"):
        candidate = Path.cwd() / name
        if candidate.is_file():
            return candidate
    return None


def load_douyin_cookies(path: Optional[str] = None) -> Optional[dict]:
    """加载 Cookie 文件，支持 JSON 对象 / Netscape txt / 原始 Cookie 字符串。"""
    target = Path(path) if path else _find_cookies_file()
    if target is None or not target.is_file():
        return None
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return None
    text = text.lstrip("\ufeff")

    if text.lstrip().startswith("{"):
        try:
            obj = json.loads(text)
            if isinstance(obj, dict) and obj:
                return obj
        except Exception:
            pass

    if "# Netscape" in text and "\t" in text:
        out = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 7 and parts[5]:
                out[parts[5]] = parts[6]
        if out:
            return out

    return _parse_cookie_string(text) or None


def _douyin_pure_http_item(video_id: str, cookies: dict) -> dict:
    """登录 Cookie 直连抖音详情接口（无需 a_bogus 签名）。"""
    base = "https://www.douyin.com/aweme/v1/web/aweme/detail/"
    params = {
        "device_platform": "webapp",
        "aid": "6383",
        "channel": "channel_pc_web",
        "aweme_id": video_id,
        "request_source": "600",
        "origin_type": "video_page",
        "pc_client_type": "1",
        "version_code": "190500",
        "version_name": "19.5.0",
        "cookie_enabled": "true",
        "screen_width": "2048",
        "screen_height": "1152",
        "browser_language": "zh-CN",
        "browser_platform": "Win32",
        "browser_name": "Edge",
        "browser_version": "151.0.0.0",
        "browser_online": "true",
        "engine_name": "Blink",
        "engine_version": "151.0.0.0",
        "os_name": "Windows",
        "os_version": "10",
        "cpu_core_num": "20",
        "device_memory": "32",
        "platform": "PC",
        "downlink": "10",
        "effective_type": "4g",
        "round_trip_time": "0",
    }
    headers = {
        "User-Agent": _EDGE_UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": f"https://www.douyin.com/video/{video_id}",
    }
    try:
        from curl_cffi import requests as cffi_requests

        resp = cffi_requests.get(
            base,
            params=params,
            headers=headers,
            cookies=cookies,
            impersonate="chrome",
            timeout=_COOKIE_TIMEOUT,
        )
    except Exception:
        # curl_cffi 不可用或 TLS 被重置时退回 httpx；
        # httpx 会读取 HTTP_PROXY/HTTPS_PROXY，适合容器内走代理的场景。
        with httpx.Client(timeout=_COOKIE_TIMEOUT) as client:
            resp = client.get(base, params=params, headers=headers, cookies=cookies)
    if resp.status_code != 200:
        raise RuntimeError(f"抖音接口返回 HTTP {resp.status_code}: {resp.text[:100]}")
    try:
        obj = resp.json()
    except Exception as exc:
        raise RuntimeError(f"抖音接口返回的不是 JSON：{resp.text[:100]}") from exc
    item = obj.get("aweme_detail")
    if not item:
        raise RuntimeError("抖音接口未返回 aweme_detail，Cookie 可能已过期或失效")
    return item


def _candidate_channels() -> list:
    """浏览器 channel 探测顺序：内置 Chromium 优先，避免无效探测。"""
    channels: list = []
    if _BROWSER_CHANNEL:
        channels.append(_BROWSER_CHANNEL)
    channels.append(None)
    if not _BROWSER_CHANNEL:
        channels.extend(["msedge", "chrome"])
    return channels


# ---- 浏览器常驻复用状态（进程内单例，lazy 启动 + 定期回收） ----
_browser_lock = threading.Lock()
_pw = None
_browser = None
_context = None
_browser_channel = None
_browser_launched_at = 0.0
_browser_use_count = 0


def _release_reusable_browser() -> None:
    """关闭常驻浏览器与 Playwright，重置状态。"""
    global _pw, _browser, _context, _browser_channel
    global _browser_launched_at, _browser_use_count
    for obj in (_context, _browser):
        if obj is not None:
            try:
                obj.close()
            except Exception:
                pass
    if _pw is not None:
        try:
            _pw.stop()
        except Exception:
            pass
        _log("browser", "常驻浏览器已回收")
    _pw = _browser = _context = None
    _browser_channel = None
    _browser_launched_at = 0.0
    _browser_use_count = 0


def _get_reusable_context():
    """懒加载常驻 Chromium context（headless），超生命周期/次数自动重建。

    调用方需已持有 _browser_lock。
    """
    global _pw, _browser, _context, _browser_channel
    global _browser_launched_at, _browser_use_count
    from playwright.sync_api import sync_playwright

    now = time.monotonic()
    need_rebuild = (
        _browser is None
        or _context is None
        or (now - _browser_launched_at) > _BROWSER_REUSE_TTL
        or _browser_use_count >= _BROWSER_REUSE_MAX
    )
    if need_rebuild:
        _release_reusable_browser()
        last_error = "无法启动任何可用浏览器"
        pw = sync_playwright().start()
        launched = False
        for channel in _candidate_channels():
            try:
                launch_kwargs = {
                    "headless": True,
                    "args": ["--disable-blink-features=AutomationControlled"],
                }
                if channel:
                    launch_kwargs["channel"] = channel
                browser = pw.chromium.launch(**launch_kwargs)
            except Exception as exc:
                last_error = f"启动 {channel or '内置Chromium'} 失败：{exc}"
                continue
            try:
                context = browser.new_context(
                    locale="zh-CN",
                    viewport={"width": 1280, "height": 800},
                )
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                try:
                    browser.close()
                except Exception:
                    pass
                continue
            _pw = pw
            _browser = browser
            _context = context
            _browser_channel = channel
            _browser_launched_at = now
            _browser_use_count = 0
            launched = True
            _log("browser", f"常驻浏览器已启动 channel={channel or '内置Chromium'}")
            break
        if not launched:
            try:
                pw.stop()
            except Exception:
                pass
            raise RuntimeError(last_error)
    _browser_use_count += 1
    return _context


def _extract_from_context(context, detail_url: str) -> dict:
    """在给定 context 上开新 page，捕获视频详情接口数据；返回 item 或抛异常。"""
    page = context.new_page()
    captured: dict = {}

    def on_response(resp) -> None:
        if "/aweme/v1/web/aweme/detail/" in resp.url and resp.status == 200:
            try:
                data = resp.json()
            except Exception:
                return
            if isinstance(data, dict) and data.get("aweme_detail"):
                captured["item"] = data["aweme_detail"]

    try:
        page.on("response", on_response)
        page.goto(
            detail_url,
            wait_until="domcontentloaded",
            timeout=_BROWSER_GOTO_TIMEOUT,
        )
        deadline = time.monotonic() + _BROWSER_POLL_TIMEOUT
        while "item" not in captured and time.monotonic() < deadline:
            page.wait_for_timeout(500)
        if "item" in captured:
            return captured["item"]
        raise RuntimeError("页面已打开，但未捕获到视频详情接口（可能触发验证码）")
    finally:
        try:
            page.close()
        except Exception:
            pass


def _douyin_browser_extract(video_id: str, headless: bool) -> dict:
    """用 Playwright 驱动 Edge/Chrome 打开抖音 PC 页，捕获详情接口数据。"""
    from playwright.sync_api import sync_playwright

    detail_url = f"https://www.douyin.com/video/{video_id}"

    # 无头模式默认复用常驻浏览器，避免每请求冷启动（1~3s 开销）。
    if headless and _BROWSER_REUSE:
        with _browser_lock:
            context = _get_reusable_context()
            return _extract_from_context(context, detail_url)

    # 有头模式（或禁用复用）：保持每次启动/关闭的独立浏览器。
    last_error = "无法启动任何可用浏览器"
    with sync_playwright() as p:
        for channel in _candidate_channels():
            try:
                launch_kwargs = {
                    "headless": headless,
                    "args": ["--disable-blink-features=AutomationControlled"],
                }
                if channel:
                    launch_kwargs["channel"] = channel
                browser = p.chromium.launch(**launch_kwargs)
            except Exception as exc:
                last_error = f"启动 {channel or '内置Chromium'} 失败：{exc}"
                continue
            try:
                context = browser.new_context(
                    locale="zh-CN",
                    viewport={"width": 1280, "height": 800},
                )
                return _extract_from_context(context, detail_url)
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            finally:
                try:
                    browser.close()
                except Exception:
                    pass
    raise RuntimeError(last_error)


def _extract_item_with_retry(video_id: str, headed: bool = False) -> dict:
    """优先无头模式，失败后使用有头模式（会短暂弹出浏览器窗口）。"""
    started = time.monotonic()
    if headed:
        item = _douyin_browser_extract(video_id, headless=False)
        _log(
            "browser",
            f"成功(有头) video_id={video_id} 耗时={time.monotonic() - started:.2f}s",
        )
        return item
    try:
        item = _douyin_browser_extract(video_id, headless=True)
        _log(
            "browser",
            f"成功(无头) video_id={video_id} 耗时={time.monotonic() - started:.2f}s",
        )
        return item
    except Exception as headless_exc:
        _log(
            "browser",
            f"无头失败 耗时={time.monotonic() - started:.2f}s: {headless_exc}；转有头重试",
        )
        item = _douyin_browser_extract(video_id, headless=False)
        _log(
            "browser",
            f"成功(有头重试) video_id={video_id} 耗时={time.monotonic() - started:.2f}s",
        )
        return item


def _item_to_videoinfo(item: dict) -> VideoInfo:
    images = []
    for img in item.get("images") or []:
        if not isinstance(img, dict):
            continue
        image_url = _first_non_webp(img.get("url_list") or [])
        live_photo_url = ""
        live_list = (
            ((img.get("video") or {}).get("play_addr") or {}).get("url_list") or []
        )
        if live_list:
            live_photo_url = live_list[0]
        if image_url:
            images.append(ImgInfo(url=image_url, live_photo_url=live_photo_url))

    video = item.get("video") or {}
    play_addr = video.get("play_addr") or {}
    play_urls = play_addr.get("url_list") or []

    video_url = ""
    music_url = ""
    if play_urls:
        if images:
            # 图集：没有视频地址，music_url 用 play_addr.uri（与原生行为一致）
            music_url = play_addr.get("uri", "")
        else:
            video_url = play_urls[0].replace("playwm", "play")

    cover_url = _first_non_webp((video.get("cover") or {}).get("url_list") or [])
    author = item.get("author") or {}
    avatar_list = (author.get("avatar_thumb") or {}).get("url_list") or []

    return VideoInfo(
        video_url=video_url,
        cover_url=cover_url,
        music_url=music_url,
        title=item.get("desc") or "",
        images=images,
        author=VideoAuthor(
            uid=author.get("sec_uid") or "",
            name=author.get("nickname") or "",
            avatar=avatar_list[0] if avatar_list else "",
        ),
    )


async def resolve_douyin_video_id(share_url: str) -> str:
    """把 v.douyin.com 短链解析成视频 ID。"""
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=20,
        headers={"User-Agent": _DOUYIN_MOBILE_UA},
    ) as client:
        response = await client.get(share_url)
    final_url = str(response.url)
    parsed = urlparse(final_url)
    query = parse_qs(parsed.query)
    if "modal_id" in query:
        return query["modal_id"][0]
    parts = [p for p in parsed.path.split("/") if p]
    return parts[-1] if parts else ""


async def _resolve_video_redirect_url(video_url: str) -> str:
    """与原生行为一致：取播放地址的重定向最终地址。"""
    if not video_url:
        return ""
    try:
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=15,
            headers={"User-Agent": _DOUYIN_MOBILE_UA},
        ) as client:
            response = await client.get(video_url)
        return response.headers.get("location") or video_url
    except Exception:
        return video_url


async def parse_douyin_fallback(
    video_id: str,
    *,
    cookies_path: Optional[str] = None,
    force_browser: bool = False,
    headed: bool = False,
) -> VideoInfo:
    """
    抖音兜底解析入口：优先登录 Cookie 纯 HTTP，失败或无 Cookie 时用浏览器。

    :param video_id: 抖音视频 ID
    :param cookies_path: 登录 Cookie 文件路径；缺省时按环境变量/当前目录自动查找
    :param force_browser: 跳过 Cookie 直连，强制使用浏览器
    :param headed: 浏览器以有头模式运行（会弹窗）
    """
    if not video_id:
        raise ValueError("抖音视频 ID 为空")

    allow_browser = os.environ.get("PARSE_VIDEO_PY_DOUYIN_NO_BROWSER") != "1"
    cookies = None if force_browser else load_douyin_cookies(cookies_path)

    if cookies:
        retries = _COOKIE_RETRIES if _COOKIE_RETRIES >= 0 else 0
        attempt = 0
        while True:
            attempt += 1
            started = time.monotonic()
            try:
                item = await asyncio.to_thread(_douyin_pure_http_item, video_id, cookies)
                info = _item_to_videoinfo(item)
                if info.video_url:
                    info.video_url = await _resolve_video_redirect_url(info.video_url)
                _log(
                    "cookie",
                    f"成功 video_id={video_id} "
                    f"耗时={time.monotonic() - started:.2f}s",
                )
                return info
            except Exception as exc:
                _log(
                    "cookie",
                    f"抖音 Cookie 直连失败 第{attempt}次 "
                    f"耗时={time.monotonic() - started:.2f}s: {exc}",
                )
                if attempt <= retries:
                    await asyncio.sleep(3 * attempt)
                    continue
                break

    if not allow_browser:
        raise RuntimeError(
            "抖音 Cookie 直连失败且已通过 PARSE_VIDEO_PY_DOUYIN_NO_BROWSER 禁用浏览器兜底"
        )

    item = await asyncio.to_thread(_extract_item_with_retry, video_id, headed)
    info = _item_to_videoinfo(item)
    if info.video_url:
        info.video_url = await _resolve_video_redirect_url(info.video_url)
    return info
