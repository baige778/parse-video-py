import os
import re
from urllib.parse import parse_qs, urlparse

import fake_useragent
import httpx

URL_REG = re.compile(r"http[s]?:\/\/[\w.-]+[\w\/-]*[\w.-]*\??[\w=&:\-\+\%.]*[/]*")


def extract_url(text: str) -> str | None:
    """从文本中提取第一个匹配的 URL"""
    match = URL_REG.search(text)
    return match.group() if match else None


def get_val_from_url_by_query_key(url: str, query_key: str) -> str:
    """
    从url的query参数中解析出query_key对应的值
    :param url: url地址
    :param query_key: query参数的key
    :return:
    """
    url_res = urlparse(url)
    url_query = parse_qs(url_res.query, keep_blank_values=True)

    try:
        query_val = url_query[query_key][0]
    except KeyError:
        raise KeyError(f"url中不存在query参数: {query_key}")

    if len(query_val) == 0:
        raise ValueError(f"url中query参数值长度为0: {query_key}")

    return url_query[query_key][0]


# ---- 全局共享 httpx 客户端（长连接复用，避免每请求重复 TCP/TLS 握手） ----
_shared_client: httpx.AsyncClient | None = None


def _get_shared_client() -> httpx.AsyncClient:
    """惰性创建全局共享 client，复用连接池。"""
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        proxy = os.getenv("PARSE_VIDEO_PROXY")
        _shared_client = httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(10.0, connect=5.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            proxy=proxy or None,
        )
    return _shared_client


async def close_shared_client() -> None:
    """应用退出时关闭共享 client。"""
    global _shared_client
    if _shared_client is not None and not _shared_client.is_closed:
        await _shared_client.aclose()
    _shared_client = None


class _SharedClientSession:
    """复用全局连接池的轻量会话，保持 `async with create_async_client(...)` 用法不变。"""

    def __init__(self, **defaults):
        self._defaults = defaults

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False  # 不关闭全局 client，保持连接复用

    async def get(self, url, **kwargs):
        return await _get_shared_client().get(url, **{**self._defaults, **kwargs})

    async def post(self, url, **kwargs):
        return await _get_shared_client().post(url, **{**self._defaults, **kwargs})


def create_async_client(**kwargs) -> _SharedClientSession:
    """返回复用全局连接池的会话（兼容原有 async with 用法）。

    会话级参数（follow_redirects / timeout 等）会作为该会话内请求的默认值。
    """
    return _SharedClientSession(**kwargs)


def create_dedicated_async_client(**kwargs) -> httpx.AsyncClient:
    """创建独立 httpx.AsyncClient（用于流式下载等需显式 close 的场景）。"""
    proxy = os.getenv("PARSE_VIDEO_PROXY")
    if proxy:
        kwargs.setdefault("proxy", proxy)
    return httpx.AsyncClient(**kwargs)


# ---- fake_useragent 单例缓存（避免每次请求重新初始化/读盘） ----
_ua_cache: dict = {}


def random_user_agent(os_name: str | None = None) -> str:
    key = os_name or "default"
    ua = _ua_cache.get(key)
    if ua is None:
        ua = (
            fake_useragent.UserAgent(os=os_name)
            if os_name
            else fake_useragent.UserAgent()
        )
        _ua_cache[key] = ua
    return ua.random