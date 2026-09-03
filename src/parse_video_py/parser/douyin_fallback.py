# -*- coding: utf-8 -*-
"""
抖音网页端风控兜底解析（纯浏览器，方案A）

抖音 web 端已启用 JS 风控（a_bogus / __ac_signature / 浏览器指纹），
原生分享页 HTML 解析可能失效。本模块统一走持久化登录态浏览器兜底：

  使用磁盘持久化 user_data_dir profile 的常驻 Chromium 打开 PC 视频页，
  让页面 JS 自行完成风控，同时复用已登录的设备指纹/会话，从接口响应中提取数据。

Cookie 文件直连（登录态纯 HTTP）已移除：抖音 ArgusSecurity 会返回
"Uifid Not Found"，导致该路径失效且拖慢整体解析。

环境变量：
   PARSE_VIDEO_PY_DOUYIN_NO_BROWSER=1  禁用浏览器兜底
"""

from __future__ import annotations

import os
import sys
import time
from typing import Optional
from urllib.parse import parse_qs, urlparse

import httpx

from .base import ImgInfo, VideoAuthor, VideoInfo


_DOUYIN_MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
    "Mobile/15E148 Safari/604.1"
)


def _log(tag: str, msg: str) -> None:
    """抖音解析结构化日志（stderr），带路径标记与耗时，便于排查。"""
    print(f"[parse-video-py][douyin][{tag}] {msg}", file=sys.stderr, flush=True)


def _first_non_webp(url_list) -> str:
    for url in url_list or []:
        if url and not str(url).endswith(".webp"):
            return url
    return (url_list or [""])[0] or ""


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
        timeout=5,
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
            timeout=5,
            headers={"User-Agent": _DOUYIN_MOBILE_UA},
        ) as client:
            response = await client.get(video_url)
        return response.headers.get("location") or video_url
    except Exception:
        return video_url


async def _extract_item_with_retry(video_id: str, headed: bool = False) -> dict:
    """持久化浏览器解析；无头失败后可选用有头重试（容器默认关闭）。"""
    from .. import douyin_browser

    started = time.monotonic()
    if headed:
        item = await douyin_browser.extract_item_headed(video_id)
        _log(
            "browser",
            f"成功(有头) video_id={video_id} 耗时={time.monotonic() - started:.2f}s",
        )
        return item
    try:
        item = await douyin_browser.extract_item(video_id)
        _log(
            "browser",
            f"成功(无头) video_id={video_id} 耗时={time.monotonic() - started:.2f}s",
        )
        return item
    except Exception as headless_exc:
        _log(
            "browser",
            f"无头失败 耗时={time.monotonic() - started:.2f}s: {headless_exc}",
        )
        # 回收可能已卡死/异常的常驻浏览器，保证下次冷启动是干净的
        await douyin_browser.recycle()
        if not douyin_browser.HEADED_RETRY:
            raise
        _log("browser", "转有头重试")
        item = await douyin_browser.extract_item_headed(video_id)
        _log(
            "browser",
            f"成功(有头重试) video_id={video_id} 耗时={time.monotonic() - started:.2f}s",
        )
        return item


async def parse_douyin_fallback(
    video_id: str,
    *,
    cookies_path: Optional[str] = None,
    force_browser: bool = False,
    headed: bool = False,
) -> VideoInfo:
    """
    抖音兜底解析入口：使用持久化登录态浏览器解析。

    :param video_id: 抖音视频 ID
    :param cookies_path: 已废弃（Cookie 直连已移除）
    :param force_browser: 已废弃（现在恒走浏览器）
    :param headed: 浏览器以有头模式运行（会弹窗，容器默认不支持）
    """
    if not video_id:
        raise ValueError("抖音视频 ID 为空")

    if cookies_path is not None or force_browser:
        _log(
            "fallback",
            "cookies_path / force_browser 参数已废弃：Cookie 直连已移除，恒走持久化浏览器",
        )

    if os.environ.get("PARSE_VIDEO_PY_DOUYIN_NO_BROWSER") == "1":
        raise RuntimeError(
            "Cookie 直连已移除，且已通过 PARSE_VIDEO_PY_DOUYIN_NO_BROWSER 禁用浏览器兜底"
        )

    item = await _extract_item_with_retry(video_id, headed)
    info = _item_to_videoinfo(item)
    if info.video_url:
        info.video_url = await _resolve_video_redirect_url(info.video_url)
    return info