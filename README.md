<div align="center">

# parse-video-py

Python 短视频 / 图集去水印解析服务

**26 平台视频 · 5 平台图集 · LivePhoto · Web API / CLI / MCP**

镜像托管于 GitHub Container Registry：`ghcr.io/baige778/parse-video-py`

</div>

> **二次开发声明**
>
> 本项目基于 [wujunwei928/parse-video-py](https://github.com/wujunwei928/parse-video-py) 进行
> **二次开发和优化**，主要面向抖音网页端风控（ArgusSecurity / 验证码 / 观看限制）导致的
> 解析失败与超时问题，并配合
> [astrbot_plugin_api_video_parser](https://github.com/baige778/astrbot_plugin_api_video_parser)
> 插件为 AstrBot 提供视频 / 图集解析能力。感谢原作者 [@wujunwei928](https://github.com/wujunwei928) 的贡献。
> 所有改动以本仓库为准，遇到问题可在 [Issues](https://github.com/baige778/parse-video-py/issues) 提问。

---

## 简介

- **视频解析**：26 个平台去水印直链
- **图集解析**：抖音 / 快手 / 小红书 / 皮皮虾 / 微博，含 LivePhoto（抖音、小红书）
- **三种接入方式**：FastAPI Web API（含前端页面）、CLI 命令行、[MCP](https://modelcontextprotocol.io/)（`/mcp`）
- **抖音风控兜底**：原生 HTML 解析 → 持久化登录态无头浏览器兜底，逐级降级
- **性能优化**：全局 HTTP 连接池复用、全平台 TTL 结果缓存、UA 单例缓存

> 💡 Tips
> 1. 解析失败请提供可复现的平台信息、分享链接到 [Issues](https://github.com/baige778/parse-video-py/issues)。
> 2. 请尽量使用 APP 分享链接，电脑网页版未做充分测试。

## 二次开发主要改进（相对上游）

| 改进项 | 说明 |
|---|---|
| 抖音二级解析策略 | 原生 HTML 解析 → 持久化登录态浏览器兜底，逐级降级提高成功率 |
| 持久化登录态浏览器 | 磁盘 `user_data_dir` profile 常驻 Chromium，长期持有并滚动刷新登录态 / `ttwid` / `UIFID` 等设备指纹，替代易失效的 Cookie 直连 |
| 登录态判定适配 | 抖音会话 Cookie 已由 `sessionid` 迁移为 `sid_tt` 等，登录 Cookie 识别集合扩展；登录态采用「Cookie 快判 + 首页 DOM 校验」，规避 `profile/self` 接口在无头 + 风控环境下的误报 |
| 解析失败精确报错 | 区分「登录态失效 / 验证码拦截 / 视频观看限制（审核中、仅 App 可见、需登录）/ 视频已删除」，插件可据此给出对应提示 |
| 解析前首页预热 | 打开视频页前先访问抖音首页，刷新风控 Cookie，避免 detail 接口被静默拦截 |
| 浏览器常驻复用 | headless 单例 Chromium 复用，避免每请求冷启动；按 TTL / 使用次数自动回收，卡死强制回收 |
| HTTP 连接池复用 | 全局共享 `httpx.AsyncClient`（`max_keepalive_connections=20`），流式下载走独立客户端 |
| 全平台结果缓存 | 进程内 TTL 缓存（默认 300s），降低重复解析耗时与 403 频率 |
| 移除假 `a_bogus` 签名 | 图集不再请求需真实签名的 `slidesinfo` 接口（假签名必然 403），改为直接解析 HTML |
| 结构化日志 | native / browser 全路径耗时埋点，输出 stderr |
| 全参数环境变量化 | 超时 / 重试 / 浏览器 / 缓存均可通过 `PARSE_VIDEO_PY_*` 配置 |
| 国内构建加速 | Dockerfile 默认清华 apt / PyPI 镜像与 npmmirror Playwright 下载源，家宽环境构建不卡死 |

## 支持平台

### 图集

| 平台 | 状态 |
|----|----|
| 抖音 | ✔ |
| 快手 | ✔ |
| 小红书 | ✔ |
| 皮皮虾 | ✔ |
| 微博 | ✔ |

### 图集 LivePhoto

| 平台 | 状态 |
|----|----|
| 小红书 | ✔ |
| 抖音 | ✔ |

### 视频

| 平台 | 状态 |
|----------|----|
| 小红书 | ✔ |
| 皮皮虾 | ✔ |
| 抖音短视频 | ✔ |
| 火山短视频 | ✔ |
| 皮皮搞笑 | ✔ |
| 快手短视频 | ✔ |
| 微视短视频 | ✔ |
| 西瓜视频 | ✔ |
| 最右 | ✔ |
| 梨视频 | ✔ |
| 度小视(原全民) | ✔ |
| 逗拍 | ✔ |
| 微博 | ✔ |
| 绿洲 | ✔ |
| 全民K歌 | ✔ |
| 6间房 | ✔ |
| 美拍 | ✔ |
| 新片场 | ✔ |
| 好看视频 | ✔ |
| 虎牙 | ✔ |
| AcFun | ✔ |
| 央视网 | ✔ |
| 搜狐视频 | ✔ |
| 哔哩哔哩 | ✔ |
| 腾讯视频 | ✔ |
| Twitter/X | ✔ |

## Docker 部署（推荐）

### 获取镜像（GHCR）

```bash
docker pull ghcr.io/baige778/parse-video-py:latest
# 或指定版本
docker pull ghcr.io/baige778/parse-video-py:v0.0.12
```

> 公开仓库的 GHCR 镜像可匿名拉取；若提示未授权，先
> `echo <GITHUB_TOKEN> | docker login ghcr.io -u <用户名> --password-stdin`。

### 运行容器（端口 8000）

```bash
docker run -d -p 8000:8000 --name parse-video \
  -v parse-video-data:/data \
  ghcr.io/baige778/parse-video-py:latest
```

> 建议挂载 `/data`（浏览器 profile、登录二维码等持久化数据），重新创建容器后登录态不丢失。

### Docker Compose 一键部署

仓库自带 `docker-compose.yml` 和 `Dockerfile`（已集成抖音兜底：持久化登录态无头浏览器）。

1. 拉取代码：

```bash
git clone https://github.com/baige778/parse-video-py.git
cd parse-video-py
```

2. 启动（使用 GHCR 镜像）或构建启动（使用本地源码）：

```bash
docker compose up -d            # 拉取 GHCR 镜像启动
docker compose up -d --build    # 本地构建启动（首次几分钟）
```

3. （可选，推荐）扫码登录抖音，让浏览器 profile 持有登录态：

   浏览器访问 `http://127.0.0.1:8000/douyin/login/qrcode`（若已开启 Basic Auth 需先输入账号密码），
   用抖音 App 扫码，登录成功后登录态即持久化到 `/data/browser_profile`。也可以访问
   `http://127.0.0.1:8000/` 在前端页面点击「抖音登录」。

4. 验证：

```bash
# B 站（无防盗链，先验证链路）
curl "http://127.0.0.1:8000/video/share/url/parse?url=https%3A%2F%2Fwww.bilibili.com%2Fvideo%2FBV1GJ411x7h7"
# 抖音（验证浏览器兜底）
curl "http://127.0.0.1:8000/video/share/url/parse?url=https%3A%2F%2Fv.douyin.com%2Fea5m6Jb8z-4%2F"
```

返回 `{"code":200,...}` 即正常。浏览器访问 `http://127.0.0.1:8000/` 可查看前端页面。

### 更新部署

```bash
docker compose pull && docker compose up -d   # GHCR 镜像方式
git pull origin main && docker compose up -d --build   # 源码构建方式
```

### 构建参数（仅源码构建）

| 构建参数 | 默认值 | 说明 |
|---|---|---|
| `APT_MIRROR` | 清华源 | apt 镜像，海外构建可置空 |
| `PYPI_MIRROR` | 清华源 | PyPI 镜像（`UV_DEFAULT_INDEX`），海外构建可置空 |
| `PLAYWRIGHT_DOWNLOAD_HOST` | npmmirror | Chromium 下载源 |
| `INSTALL_BROWSER` | `1` | 设为 `0` 不安装无头 Chromium（将失去抖音兜底） |

## 本地运行

### 使用 uv（推荐）

```shell
cd parse-video-py
uv venv && uv pip install -e ".[all]"
source .venv/bin/activate
```

抖音兜底依赖（playwright）包含在 `.[all]` 中；单独安装：`uv pip install -e ".[douyin]"`。

### CLI 命令行

```shell
parse-video-py parse "https://v.douyin.com/xxx"            # 解析视频
parse-video-py parse "https://v.douyin.com/xxx" --format json
parse-video-py serve --port 8000                           # 启动 Web 服务
parse-video-py version
```

### 启动 Web 服务

```shell
uvicorn parse_video_py.web:app --reload
```

## 抖音解析与登录（风控兜底）

抖音网页端已启用 JS 风控（`a_bogus` / `__ac_signature` / 浏览器指纹），原生分享页解析可能失效。
本仓库采用**磁盘持久化登录态的常驻浏览器**兜底：

- 使用 Playwright 无头 Chromium 打开抖音 PC 视频页，让页面 JS 自行完成风控；
- 浏览器 profile 落地到磁盘（默认 `/data/browser_profile`），登录态 / `ttwid` /
  `UIFID` 等设备指纹由浏览器自动滚动刷新，长期有效；
- 通过 `/douyin/login/qrcode` 扫码登录一次后，登录态持久化到该 profile，
  后续解析直接复用，无需再导出或维护 Cookie 文件。

常见失败原因与提示对照：

| 报错 | 含义与处理 |
|---|---|
| `抖音登录态已失效，请重新扫描二维码登录` | profile 无任何登录 Cookie，扫码登录即可 |
| `页面已打开，但未捕获到视频详情接口（可能触发验证码）` | 触发风控验证码，稍后重试或更换网络 |
| `视频存在观看限制（审核中/仅App可见/需登录等），暂无法解析` | 视频在网页端被限制观看，网页渠道无法获取，非服务故障 |
| `视频不存在或已删除` | 视频已删除 / 私密 |

## 对接 AstrBot 插件

配套插件 [astrbot_plugin_api_video_parser](https://github.com/baige778/astrbot_plugin_api_video_parser) 配置
`parser_api_base_url`：

- 插件与解析服务在同一台机器：`http://127.0.0.1:8000`
- 插件在其他设备：`http://<解析服务所在机器IP>:8000`

插件会把抖音视频下载到本地后以文件方式发送（已处理 CDN 防盗链），B 站等其他平台直发视频 URL。

## 环境变量

### 抖音解析（风控兜底）相关

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `DOUYIN_DATA_DIR` | `/data` | 数据目录（浏览器 profile、登录二维码等） |
| `DOUYIN_LOGIN_TIMEOUT` | `300` | 抖音扫码登录超时（秒） |
| `PARSE_VIDEO_PY_DOUYIN_PROFILE_DIR` | `<data>/browser_profile` | 持久化浏览器 profile 目录 |
| `PARSE_VIDEO_PY_DOUYIN_NO_BROWSER` | 空 | 设为 `1` 禁用浏览器兜底（抖音将无法兜底解析） |
| `PARSE_VIDEO_PY_DOUYIN_NATIVE_TIMEOUT` | - | 原生解析超时 |
| `PARSE_VIDEO_PY_DOUYIN_REDIRECT_TIMEOUT` | - | 短链重定向解析超时 |
| `PARSE_VIDEO_PY_DOUYIN_BROWSER_GOTO_TIMEOUT` | `10000` | 浏览器打开页面超时（毫秒） |
| `PARSE_VIDEO_PY_DOUYIN_BROWSER_POLL_TIMEOUT` | `8000` | 浏览器等待抓取接口超时（毫秒） |
| `PARSE_VIDEO_PY_DOUYIN_BROWSER_CHANNEL` | 空 | 浏览器 channel（如 `msedge`/`chrome`；留空内置 Chromium 优先） |
| `PARSE_VIDEO_PY_DOUYIN_BROWSER_REUSE_TTL` | `600` | 常驻浏览器生命周期（秒） |
| `PARSE_VIDEO_PY_DOUYIN_BROWSER_REUSE_MAX` | `100` | 常驻浏览器最大复用次数 |
| `PARSE_VIDEO_PY_DOUYIN_BROWSER_LOCK_TIMEOUT` | `20` | 浏览器锁等待超时（秒，超时判定卡死并回收） |
| `PARSE_VIDEO_PY_DOUYIN_BROWSER_HEADED_RETRY` | `0` | 无头失败是否转有头重试（容器无显示应关闭） |
| `PARSE_VIDEO_PY_DOUYIN_CACHE_TTL` | `300` | 抖音结果缓存 TTL（秒，`0` 关闭） |
| `PARSE_VIDEO_PY_CACHE_TTL` | `300` | 全平台结果缓存 TTL（秒，`0` 关闭） |

### Web / 认证 / 代理

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `PARSE_VIDEO_USERNAME` | 空 | Basic Auth 用户名 |
| `PARSE_VIDEO_PASSWORD` | 空 | Basic Auth 密码 |
| `PARSE_VIDEO_PROXY` | 空 | HTTP 代理（直连则留空） |

## 接口与返回格式

前端页面：`http://127.0.0.1:8000/`

```bash
curl 'http://127.0.0.1:8000/video/share/url/parse?url=视频分享链接' | jq
```

返回格式：

```json
{
  "author": {
    "uid": "uid",
    "name": "name",
    "avatar": "https://xxx"
  },
  "title": "记录美好生活#峡谷天花板",
  "video_url": "https://xxx",
  "music_url": "https://yyy",
  "cover_url": "https://zzz"
}
```

| 字段名 | 说明 |
| ---- | ---- |
| author.uid | 视频作者 id |
| author.name | 视频作者名称 |
| author.avatar | 视频作者头像 |
| title | 视频标题 |
| video_url | 视频无水印链接 |
| music_url | 视频音乐链接 |
| cover_url | 视频封面 |
| images | 图集图片列表 |
| images.[index].url | 图集图片地址 |
| images.[index].live_photo_url | 图集图片 livephoto 视频地址 |

> 字段除了视频地址，其他字段可能为空。

## 自行调用方法

```python
import json
import asyncio

from parse_video_py import parse_video_share_url, parse_video_id, VideoSource

# 根据分享链接解析
video_info = asyncio.run(parse_video_share_url("分享链接"))
print(json.dumps(video_info, ensure_ascii=False, indent=4, default=lambda x: x.__dict__))

# 根据视频 id 解析
video_info = asyncio.run(parse_video_id(VideoSource.DouYin, "视频ID"))
print(json.dumps(video_info, ensure_ascii=False, indent=4, default=lambda x: x.__dict__))
```

## 常见问题

- **容器内解析报 `httpx.ConnectError` / SSL 错误而宿主机正常**：WSL2 网络问题，
  将 WSL 虚拟网卡 MTU 改为 1350 并重启 Docker Desktop，或关闭代理的 TUN 模式；
- **抖音兜底解析失败**：先确认常驻浏览器 profile 是否含有效登录态，可重新访问
  `/douyin/login/qrcode` 扫码登录刷新；若容器无显示且报浏览器启动失败，检查
  `playwright` 内置 Chromium 是否已安装（镜像内默认安装）；
- **源码构建卡在依赖下载**：家宽直连 PyPI 可能被断连，镜像默认已走清华源；
  海外环境可 `--build-arg PYPI_MIRROR= --build-arg APT_MIRROR=` 置空；
- **抖音视频超过 50MB 不发送**：插件侧 `video_max_size_mb` 限制，在插件配置中调大即可。

## 依赖模块

| 模块 | 作用 |
|-------------|--------------------------------------|
| fastapi | Web 框架 |
| fastapi-mcp | 支持 MCP |
| httpx | HTTP 和 REST 客户端 |
| parsel | 解析 html 页面 |
| playwright | 抖音持久化登录态浏览器兜底 |
| pre-commit | git 提交前检查（flake8 / isort / black） |
| flake8 | 代码风格一致性 |
| isort | 格式化导入顺序 |
| black | 代码格式化 |

## 更新日志

详见 [CHANGELOG.md](./CHANGELOG.md)。

## License

[MIT](./LICENSE)
