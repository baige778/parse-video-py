import json
import os
import re
import time
from typing import Dict, Tuple
from urllib.parse import parse_qs, urlparse

from ..utils import create_async_client
from .base import BaseParser, ImgInfo, VideoAuthor, VideoInfo


def _env_int(name: str, default: int) -> int:
    """从环境变量读取整数配置，解析失败时回退默认值。"""
    try:
        return int((os.environ.get(name) or "").strip() or default)
    except (TypeError, ValueError):
        return default


# ---- 抖音解析结果缓存（进程内，TTL 过期自动失效；降低重复解析与 403 频率） ----
_CACHE_TTL = _env_int("PARSE_VIDEO_PY_DOUYIN_CACHE_TTL", 300)
_video_cache: Dict[str, Tuple[float, VideoInfo]] = {}


def _cache_get(video_id: str):
    if not video_id or _CACHE_TTL <= 0:
        return None
    item = _video_cache.get(video_id)
    if item and time.monotonic() - item[0] < _CACHE_TTL:
        return item[1]
    return None


def _cache_set(video_id: str, info: VideoInfo) -> None:
    if video_id and _CACHE_TTL > 0:
        _video_cache[video_id] = (time.monotonic(), info)


class DouYin(BaseParser):
    """
    抖音 / 抖音火山版
    """

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        """解析分享链接；原生页面解析失败时自动启用风控兜底。"""
        # 预先解析 video_id（短链仅请求一次），原生与兜底复用，
        # 避免原生失败后兜底重复请求短链。
        video_id = await self._resolve_video_id(share_url)

        cached = _cache_get(video_id)
        if cached is not None:
            return cached

        try:
            info = await self._parse_share_url_native(share_url, video_id=video_id)
        except Exception as native_exc:
            try:
                from .douyin_fallback import parse_douyin_fallback

                if not video_id:
                    from .douyin_fallback import resolve_douyin_video_id

                    video_id = await resolve_douyin_video_id(share_url)
                info = await parse_douyin_fallback(video_id)
            except Exception as fb_exc:
                raise RuntimeError(
                    f"抖音原生解析失败({type(native_exc).__name__}): {native_exc}\n"
                    f"兜底解析也失败({type(fb_exc).__name__}): {fb_exc}"
                ) from fb_exc

        _cache_set(video_id, info)
        return info

    async def _resolve_video_id(self, share_url: str) -> str:
        """从分享链接解析视频 ID；短链需一次请求，PC 链接为本地解析。失败返回空串。"""
        host = urlparse(share_url).netloc
        try:
            if host in ["www.iesdouyin.com", "www.douyin.com"]:
                return self._parse_video_id_from_path(share_url)
            if host == "v.douyin.com":
                return await self._parse_app_share_url(share_url)
        except Exception:
            pass
        return ""

    async def _parse_share_url_native(
        self, share_url: str, video_id: str = ""
    ) -> VideoInfo:
        # 解析URL获取域名
        parsed_url = urlparse(share_url)
        host = parsed_url.netloc

        if host in ["www.iesdouyin.com", "www.douyin.com"]:
            # 支持电脑网页端链接
            if not video_id:
                video_id = self._parse_video_id_from_path(share_url)
            if not video_id:
                raise ValueError("Failed to parse video ID from PC share URL")
            share_url = self._get_request_url_by_video_id(video_id)
        elif host == "v.douyin.com":
            # 支持app分享链接 https://v.douyin.com/xxxxxx
            if not video_id:
                video_id = await self._parse_app_share_url(share_url)
            if not video_id:
                raise ValueError("Failed to parse video ID from app share URL")
            share_url = self._get_request_url_by_video_id(video_id)
        else:
            raise ValueError(f"Douyin not support this host: {host}")

        async with create_async_client(follow_redirects=True) as client:
            response = await client.get(share_url, headers=self.get_default_headers())
            response.raise_for_status()

        # 直接解析分享页内的 window._ROUTER_DATA，同时覆盖视频与图集两种结构；
        # 不再请求需要 a_bogus 真实签名的 slidesinfo 接口（假签名必然失败）。
        pattern = re.compile(
            pattern=r"window\._ROUTER_DATA\s*=\s*(.*?)</script>",
            flags=re.DOTALL,
        )
        find_res = pattern.search(response.text)

        if not find_res or not find_res.group(1):
            raise ValueError("parse video json info from html fail")

        json_data = json.loads(find_res.group(1).strip())

        # 处理 HTML 解析返回的 loaderData 数据结构
        data = None
        if isinstance(json_data, dict) and "loaderData" in json_data:
            VIDEO_ID_PAGE_KEY = "video_(id)/page"
            NOTE_ID_PAGE_KEY = "note_(id)/page"

            original_video_info = None
            if VIDEO_ID_PAGE_KEY in json_data["loaderData"]:
                original_video_info = json_data["loaderData"][VIDEO_ID_PAGE_KEY][
                    "videoInfoRes"
                ]
            elif NOTE_ID_PAGE_KEY in json_data["loaderData"]:
                original_video_info = json_data["loaderData"][NOTE_ID_PAGE_KEY][
                    "videoInfoRes"
                ]
            else:
                raise Exception(
                    "failed to parse Videos or Photo Gallery info from json"
                )

            # 如果没有视频信息，获取并抛出异常
            if len(original_video_info["item_list"]) == 0:
                err_detail_msg = "failed to parse video info from HTML"
                if len(filter_list := original_video_info["filter_list"]) > 0:
                    err_detail_msg = filter_list[0]["detail_msg"]
                raise Exception(err_detail_msg)

            data = original_video_info["item_list"][0]
        else:
            raise Exception("Unknown data structure")

        if not data:
            raise Exception("Failed to extract data from response")

        # 获取图集图片地址
        images = []
        # 如果data含有 images，并且 images 是一个列表
        if "images" in data and isinstance(data["images"], list):
            # 获取每个图片的url_list中的第一个元素，优先获取非 .webp 格式的图片 url
            for img in data["images"]:
                if (
                    "url_list" in img
                    and isinstance(img["url_list"], list)
                    and len(img["url_list"]) > 0
                ):
                    image_url = self._get_no_webp_url(img["url_list"])
                    if image_url:
                        live_photo_url = ""
                        if (
                            "video" in img
                            and "play_addr" in img["video"]
                            and "url_list" in img["video"]["play_addr"]
                        ):
                            live_photo_url = (
                                img["video"]["play_addr"]["url_list"][0]
                                if img["video"]["play_addr"]["url_list"]
                                else ""
                            )
                        images.append(
                            ImgInfo(url=image_url, live_photo_url=live_photo_url)
                        )

        # 获取视频和音频播放地址
        video_url = ""
        music_url = ""
        if "video" in data and "play_addr" in data["video"]:
            if "url_list" in data["video"]["play_addr"]:
                video_url = data["video"]["play_addr"]["url_list"][0].replace(
                    "playwm", "play"
                )
            music_url = data["video"]["play_addr"].get("uri", "")

        # 如果图集地址不为空时，因为没有视频，上面抖音返回的视频地址无法访问，置空处理
        if len(images) > 0:
            video_url = ""
        else:
            # 图集时, video.play_addr.uri 是音频地址; 视频时不是
            music_url = ""

        # 获取重定向后的mp4视频地址
        # 图集时，视频地址为空，不处理
        video_mp4_url = ""
        if len(video_url) > 0:
            video_mp4_url = await self.get_video_redirect_url(video_url)

        # 获取封面图片，优先获取非 .webp 格式的图片 url
        cover_url = ""
        if (
            "video" in data
            and "cover" in data["video"]
            and "url_list" in data["video"]["cover"]
        ):
            cover_url = self._get_no_webp_url(data["video"]["cover"]["url_list"])

        video_info = VideoInfo(
            video_url=video_mp4_url,
            cover_url=cover_url,
            music_url=music_url,
            title=data.get("desc", ""),
            images=images,
            author=VideoAuthor(
                uid=data.get("author", {}).get("sec_uid", ""),
                name=data.get("author", {}).get("nickname", ""),
                avatar=(
                    data.get("author", {})
                    .get("avatar_thumb", {})
                    .get("url_list", [""])[0]
                    if data.get("author", {}).get("avatar_thumb", {}).get("url_list")
                    else ""
                ),
            ),
        )
        return video_info

    async def get_video_redirect_url(self, video_url: str) -> str:
        async with create_async_client(follow_redirects=False) as client:
            response = await client.get(video_url, headers=self.get_default_headers())
        # 返回重定向后的地址，如果没有重定向则返回原地址(抖音中的西瓜视频,重定向地址为空)
        return response.headers.get("location") or video_url

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        """按视频 ID 解析；原生失败时自动启用风控兜底。"""
        cached = _cache_get(video_id)
        if cached is not None:
            return cached
        try:
            req_url = self._get_request_url_by_video_id(video_id)
            info = await self.parse_share_url(req_url)
        except Exception as native_exc:
            try:
                from .douyin_fallback import parse_douyin_fallback

                info = await parse_douyin_fallback(video_id)
            except Exception as fb_exc:
                raise RuntimeError(
                    f"抖音原生解析失败({type(native_exc).__name__}): {native_exc}\n"
                    f"兜底解析也失败({type(fb_exc).__name__}): {fb_exc}"
                ) from fb_exc

        _cache_set(video_id, info)
        return info

    def _get_request_url_by_video_id(self, video_id) -> str:
        return f"https://www.iesdouyin.com/share/video/{video_id}/"

    async def _parse_app_share_url(self, share_url: str) -> str:
        """解析app分享链接 https://v.douyin.com/xxxxxx"""
        async with create_async_client(follow_redirects=False) as client:
            response = await client.get(share_url, headers=self.get_default_headers())

        location = response.headers.get("location")
        if not location:
            return ""

        # 检查是否是西瓜视频链接
        if "ixigua.com" in location:
            # 如果是西瓜视频，这里应该返回特殊处理，暂时返回空
            # 在实际应用中可能需要调用西瓜视频解析器
            return ""

        return self._parse_video_id_from_path(location)

    def _parse_video_id_from_path(self, url_path: str) -> str:
        """从URL路径中解析视频ID"""
        if not url_path:
            return ""

        try:
            parsed_url = urlparse(url_path)

            # 判断网页精选页面的视频
            # https://www.douyin.com/jingxuan?modal_id=7555093909760789812
            query_params = parse_qs(parsed_url.query)
            if "modal_id" in query_params:
                return query_params["modal_id"][0]

            # 判断其他页面的视频
            # https://www.iesdouyin.com/share/video/7424432820954598707/?region=CN&mid=7424432976273869622&u_code=0
            # https://www.douyin.com/video/xxxxxx
            path = parsed_url.path.strip("/")
            if path:
                path_parts = path.split("/")
                if len(path_parts) > 0:
                    return path_parts[-1]
        except Exception:
            pass

        return ""

    def _get_no_webp_url(self, url_list: list) -> str:
        """优先获取非 .webp 格式的图片 url"""
        if not url_list:
            return ""

        # 优先获取非 .webp 格式的图片 url
        for url in url_list:
            if url and not url.endswith(".webp"):
                return url

        # 如果没找到，使用第一项
        return url_list[0] if url_list and url_list[0] else ""
