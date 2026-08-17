import dataclasses
import ipaddress
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from fastapi_mcp import FastApiMCP

from parse_video_py import VideoSource, parse_video_id, parse_video_share_url
from parse_video_py.douyin_login import get_douyin_login_manager
from parse_video_py.utils import create_async_client, extract_url


def _get_templates_dir() -> str:
    # 模板已移入 src/parse_video_py/templates/，与 web.py 同级
    templates_dir = Path(__file__).parent / "templates"
    if templates_dir.is_dir():
        return str(templates_dir)
    raise FileNotFoundError("templates 目录未找到")


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    # 应用退出时关闭抖音登录浏览器，释放内存
    await get_douyin_login_manager().shutdown()


app = FastAPI(lifespan=lifespan)

mcp = FastApiMCP(app)
mcp.mount_http()

templates = Jinja2Templates(directory=_get_templates_dir())


def _build_auth_dependency() -> list[Depends]:
    """根据环境变量动态构建 Basic Auth 依赖项"""
    basic_auth_username = os.getenv("PARSE_VIDEO_USERNAME")
    basic_auth_password = os.getenv("PARSE_VIDEO_PASSWORD")

    if not (basic_auth_username and basic_auth_password):
        return []

    security = HTTPBasic()

    def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
        correct_username = secrets.compare_digest(
            credentials.username, basic_auth_username
        )
        correct_password = secrets.compare_digest(
            credentials.password, basic_auth_password
        )
        if not (correct_username and correct_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect username or password",
                headers={"WWW-Authenticate": "Basic"},
            )
        return credentials

    return [Depends(verify_credentials)]


# 模块加载时构建一次，避免每个路由装饰器重复调用
_auth_dependency = _build_auth_dependency()


# ============ 下载代理（绕过抖音等 CDN 防盗链） ============
_DOWNLOAD_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

# 需要带 Referer 才能下载的防盗链 CDN（域名后缀 -> Referer）
_ANTI_LEECH_REFERERS: tuple[tuple[str, str], ...] = (
    ("douyinvod.com", "https://www.douyin.com/"),
    ("iesdouyin.com", "https://www.douyin.com/"),
    ("douyinpic.com", "https://www.douyin.com/"),
    ("douyinstatic.com", "https://www.douyin.com/"),
    ("amemv.com", "https://www.douyin.com/"),
    ("zjcdn.com", "https://www.douyin.com/"),
    ("pstatp.com", "https://www.douyin.com/"),
    ("byteimg.com", "https://www.douyin.com/"),
    ("bytecdn.cn", "https://www.douyin.com/"),
    ("snssdk.com", "https://www.douyin.com/"),
    ("muscdn.com", "https://www.douyin.com/"),
)


def _build_download_headers(url: str) -> dict:
    """为下载请求构造 headers，针对抖音等防盗链 CDN 附带正确 Referer。"""
    host = (urlparse(url).hostname or "").lower()
    headers = {"User-Agent": _DOWNLOAD_UA}
    for suffix, referer in _ANTI_LEECH_REFERERS:
        if host == suffix or host.endswith("." + suffix):
            headers["Referer"] = referer
            break
    return headers


def _is_safe_download_url(url: str) -> bool:
    """拦截内网/本地/保留地址，避免被当作 SSRF 代理滥用。"""
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True  # 域名，放行
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


@app.get("/", response_class=HTMLResponse, dependencies=_auth_dependency)
async def read_item(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "title": "github.com/baige778/parse-video-py Demo",
        },
    )


@app.get("/video/share/url/parse", dependencies=_auth_dependency)
async def share_url_parse(url: str):
    video_share_url = extract_url(url)
    if video_share_url is None:
        return {
            "code": 400,
            "msg": "未检测到有效的分享链接",
        }

    try:
        video_info = await parse_video_share_url(video_share_url)
        return {
            "code": 200,
            "msg": "解析成功",
            "data": dataclasses.asdict(video_info),
        }
    except Exception as err:
        return {
            "code": 500,
            "msg": f"{type(err).__name__}: {err}",
        }


@app.get("/video/id/parse", dependencies=_auth_dependency)
async def video_id_parse(source: VideoSource, video_id: str):
    try:
        video_info = await parse_video_id(source, video_id)
        return {
            "code": 200,
            "msg": "解析成功",
            "data": dataclasses.asdict(video_info),
        }
    except Exception as err:
        return {
            "code": 500,
            "msg": f"{type(err).__name__}: {err}",
        }


@app.get("/download", dependencies=_auth_dependency)
async def download_proxy(url: str):
    """代理下载：由后端带 Referer/UA 拉取资源并流式返回，绕过抖音等 CDN 防盗链。"""
    if not url or not url.lower().startswith(("http://", "https://")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "无效的下载地址")
    if not _is_safe_download_url(url):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "禁止下载该地址")

    headers = _build_download_headers(url)
    client = create_async_client(
        timeout=httpx.Timeout(60.0, connect=15.0),
        follow_redirects=True,
    )
    try:
        upstream = await client.send(
            client.build_request("GET", url, headers=headers), stream=True
        )
        upstream.raise_for_status()

        content_type = upstream.headers.get("content-type", "application/octet-stream")
        content_length = upstream.headers.get("content-length")
        media_type = content_type.split(";")[0].strip() or "application/octet-stream"

        async def stream():
            try:
                async for chunk in upstream.aiter_bytes(1024 * 512):
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        resp_headers = {"Cache-Control": "no-store"}
        if content_length:
            resp_headers["Content-Length"] = content_length

        return StreamingResponse(
            stream(),
            status_code=200,
            media_type=media_type,
            headers=resp_headers,
        )
    except httpx.HTTPStatusError as err:
        await client.aclose()
        raise HTTPException(
            err.response.status_code,
            f"上游下载失败: HTTP {err.response.status_code}",
        )
    except Exception as err:
        await client.aclose()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"下载失败: {err}")


@app.get("/douyin/login/qrcode", dependencies=_auth_dependency)
async def douyin_login_qrcode():
    """发起抖音扫码登录，返回二维码图片（文件路径 + base64）。"""
    return await get_douyin_login_manager().start_qrcode()


@app.get("/douyin/login/status", dependencies=_auth_dependency)
async def douyin_login_status():
    """查询抖音扫码登录状态。"""
    return await get_douyin_login_manager().status()


@app.get("/douyin/login/cancel", dependencies=_auth_dependency)
async def douyin_login_cancel():
    """取消当前抖音扫码登录并关闭浏览器。"""
    return await get_douyin_login_manager().cancel()


mcp.setup_server()
