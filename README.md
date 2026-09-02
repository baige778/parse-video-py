<div align="center">

# parse-video-py

Python 短视频/图集去水印解析服务

</div>

> **二次开发声明**
>
> 本项目基于 [wujunwei928/parse-video-py](https://github.com/wujunwei928/parse-video-py) 进行
> **二次开发和优化**，主要面向抖音网页端风控（ArgusSecurity）导致的解析失败 / 超时问题，
> 并配合 [astrbot_plugin_api_video_parser](https://github.com/baige778/astrbot_plugin_api_video_parser)
> 插件为 AstrBot 提供视频/图集解析能力。感谢原作者 [@wujunwei928](https://github.com/wujunwei928) 的贡献。
> 所有改动请以本仓库为准，遇到问题可在本仓库 [Issues](https://github.com/baige778/parse-video-py/issues) 提问。

---

## 简介

- 视频解析：支持 **26 个平台**去水印
- 图集解析：支持 **5 个平台**
- LivePhoto 解析：支持小红书、抖音
- 三种接入方式：FastAPI Web API / CLI 命令行 / [MCP](https://modelcontextprotocol.io/)

> 💡 Tips
> 1. 解析失败请提供可复现的平台信息、分享链接到 [Issues](https://github.com/baige778/parse-video-py/issues)。
> 2. 请尽量使用 APP 分享链接，电脑网页版未做充分测试。

## 二次开发主要改进（相对上游）

| 改进项 | 说明 |
|---|---|
| 抖音三级解析策略 | 原生 HTML 解析 → 登录 Cookie 纯 HTTP 直连 → 无头浏览器兜底，逐级降级提高成功率 |
| 移除假 `a_bogus` 签名 | 图集不再请求需要真实签名的 `slidesinfo` 接口（假签名必然 403），改为直接解析 HTML |
| Cookie 直连提速 | 超时从 25s 压到 5s，失败自动重试（默认 1 次，3~5s 退避） |
| 浏览器常驻复用 | headless 模式复用单例 Chromium，避免每请求 1~3s 冷启动；按 TTL / 使用次数自动回收 |
| video_id 级结果缓存 | 默认 TTL 300s，降低重复解析与 403 频率 |
| video_id 复用 | 原生失败后兜底直接复用已解析的 video_id，避免重复请求短链 |
| 结构化日志 | 所有解析路径（native / Cookie / browser）带耗时埋点，输出到 stderr |
| 全参数环境变量化 | 所有超时 / 重试 / 浏览器 / 缓存参数均可通过 `PARSE_VIDEO_PY_DOUYIN_*` 配置 |

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

## 运行

### 本地运行

#### 使用 uv（推荐）

```shell
# 进入项目根目录
cd parse-video-py

# 创建虚拟环境并安装全部依赖
uv venv && uv pip install -e ".[all]"

# 激活虚拟环境
source .venv/bin/activate
```

#### CLI 命令行

```shell
# 解析视频
parse-video-py parse "https://v.douyin.com/xxx"
parse-video-py parse "https://v.douyin.com/xxx" --format json

# 启动 Web 服务
parse-video-py serve --port 8000

# 查看版本
parse-video-py version
```

#### 抖音解析说明（风控兜底）

抖音网页端已启用 JS 风控（`a_bogus` / `__ac_signature` / 浏览器指纹），原生分享页解析可能失效。
本仓库集成两条自动兜底路径：

1. **登录 Cookie 纯 HTTP**：把登录抖音后的 Cookie 保存为 `douyin_cookies.txt`
   （支持原始 Cookie 字符串 / Netscape 格式 / JSON），或通过环境变量
   `PARSE_VIDEO_PY_DOUYIN_COOKIES` 指定路径。解析时自动直连详情接口，无需签名。
2. **无头浏览器**：没有 Cookie 或 Cookie 失效时，自动用 Playwright 驱动本机
   Edge / Chrome（或内置 Chromium）打开视频页抓取数据。

安装抖音兜底依赖：

```shell
uv pip install -e ".[douyin]"   # 包含 curl_cffi + playwright
```

Cookie 获取：浏览器登录 douyin.com 后，F12 → Network → 任意 douyin.com 请求 →
复制 Cookie 头，粘贴到 `douyin_cookies.txt`。

> 注意：Cookie 属于登录凭证，请勿提交到 git（.gitignore 已忽略）。

#### Basic Auth 认证（可选，默认不开启）

```shell
export PARSE_VIDEO_USERNAME=username
export PARSE_VIDEO_PASSWORD=password
```

#### 代理（可选，默认直连）

```shell
# 无认证代理
export PARSE_VIDEO_PROXY=http://proxy.example.com:端口

# 有认证代理
export PARSE_VIDEO_PROXY=http://user:pass@proxy.example.com:端口
```

#### 启动 Web 服务

```shell
uvicorn parse_video_py.web:app --reload
```

## Docker 运行

### 获取镜像

```bash
docker pull baige778/parse-video-py
```

### 运行容器（端口 8000）

```bash
docker run -d -p 8000:8000 --name parse-video baige778/parse-video-py
```

### Docker Compose 一键部署（推荐）

仓库自带 `docker-compose.yml` 和 `Dockerfile`（已集成抖音兜底：登录 Cookie 纯 HTTP 直连 + 可选无头浏览器）。

#### 新机器部署步骤

1. 拉取代码：

```bash
git clone https://github.com/baige778/parse-video-py.git
cd parse-video-py
```

2. 准备抖音登录 Cookie：
   浏览器登录 douyin.com → F12 → Network → 任意 douyin.com 请求 →
   复制 Cookie 头，保存为 `data/douyin_cookies.txt`
   （该文件已被 .gitignore 忽略，不会提交进 git）。

3. 构建并启动（首次构建需要几分钟）：

```bash
docker compose up -d --build
```

4. 验证：

```bash
# B 站（无防盗链，先验证链路）
curl "http://127.0.0.1:8000/video/share/url/parse?url=https%3A%2F%2Fwww.bilibili.com%2Fvideo%2FBV1GJ411x7h7"
# 抖音（验证 Cookie 直连）
curl "http://127.0.0.1:8000/video/share/url/parse?url=https%3A%2F%2Fv.douyin.com%2Fea5m6Jb8z-4%2F"
```

返回 `{"code":200,...}` 即正常。浏览器访问 `http://127.0.0.1:8000/` 可查看前端页面。

#### 更新部署

```bash
git pull origin main
docker compose up -d --build
```

#### 对接 AstrBot 插件

配套插件 [astrbot_plugin_api_video_parser](https://github.com/baige778/astrbot_plugin_api_video_parser) 配置
`parser_api_base_url`：

- 插件与解析服务在同一台机器：`http://127.0.0.1:8000`
- 插件在其他设备：`http://<解析服务所在机器IP>:8000`

插件会把抖音视频下载到本地后以文件方式发送（已处理 CDN 防盗链），B 站等其他平台直发视频 URL。

#### 常见问题

- 容器内解析报 `httpx.ConnectError` / SSL 错误而宿主机正常：WSL2 网络问题，
  将 WSL 虚拟网卡 MTU 改为 1350 并重启 Docker Desktop，或关闭代理的 TUN 模式；
- 抖音报“Cookie 直连失败且禁用浏览器”：`data/douyin_cookies.txt` 缺失或 Cookie 失效，
  刷新 Cookie 后执行 `docker compose restart`；
- 抖音视频超过 50MB 不发送：插件侧 `video_max_size_mb` 限制，在插件配置中调大即可。

### 运行容器并开启 Basic Auth

```bash
docker run -d -p 8000:8000 --name parse-video \
  -e PARSE_VIDEO_USERNAME=username \
  -e PARSE_VIDEO_PASSWORD=password \
  baige778/parse-video-py
```

### 运行容器并设置代理

```bash
docker run -d -p 8000:8000 --name parse-video \
  -e PARSE_VIDEO_PROXY=http://proxy.example.com:端口 \
  baige778/parse-video-py
```

## 环境变量

### 抖音解析（风控兜底）相关

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `PARSE_VIDEO_PY_DOUYIN_COOKIES` | 空 | 抖音登录 Cookie 文件路径（自动查找 douyin_cookies.txt/.json） |
| `PARSE_VIDEO_PY_DOUYIN_NO_BROWSER` | 空 | 设为 `1` 禁用浏览器兜底（只走 Cookie 直连） |
| `PARSE_VIDEO_PY_DOUYIN_COOKIE_TIMEOUT` | `5` | Cookie 直连超时（秒） |
| `PARSE_VIDEO_PY_DOUYIN_COOKIE_RETRIES` | `1` | Cookie 直连失败重试次数 |
| `PARSE_VIDEO_PY_DOUYIN_BROWSER_GOTO_TIMEOUT` | `15000` | 浏览器打开页面超时（毫秒） |
| `PARSE_VIDEO_PY_DOUYIN_BROWSER_POLL_TIMEOUT` | `10000` | 浏览器等待抓取接口超时（毫秒） |
| `PARSE_VIDEO_PY_DOUYIN_BROWSER_CHANNEL` | 空 | 浏览器 channel（如 `msedge`/`chrome`；留空内置 Chromium 优先） |
| `PARSE_VIDEO_PY_DOUYIN_BROWSER_REUSE` | `1` | 是否复用常驻 headless 浏览器（`0` 关闭） |
| `PARSE_VIDEO_PY_DOUYIN_BROWSER_REUSE_TTL` | `600` | 常驻浏览器生命周期（秒） |
| `PARSE_VIDEO_PY_DOUYIN_BROWSER_REUSE_MAX` | `100` | 常驻浏览器最大复用次数 |
| `PARSE_VIDEO_PY_DOUYIN_CACHE_TTL` | `300` | 解析结果缓存 TTL（秒，`0` 关闭） |

### Web / 认证 / 代理

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `PARSE_VIDEO_USERNAME` | 空 | Basic Auth 用户名 |
| `PARSE_VIDEO_PASSWORD` | 空 | Basic Auth 密码 |
| `PARSE_VIDEO_PROXY` | 空 | HTTP 代理（直连则留空） |

## 查看前端页面 / 接口

前端页面：`http://127.0.0.1:8000/`

请求接口并查看 JSON 返回：

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

## 依赖模块

| 模块 | 作用 |
|-------------|--------------------------------------|
| fastapi | Web 框架 |
| fastapi-mcp | 支持 MCP |
| httpx | HTTP 和 REST 客户端 |
| parsel | 解析 html 页面 |
| curl_cffi | 抖音 Cookie 直连（TLS 指纹模拟） |
| playwright | 抖音无头浏览器兜底 |
| pre-commit | git 提交前检查（flake8 / isort / black） |
| flake8 | 代码风格一致性 |
| isort | 格式化导入顺序 |
| black | 代码格式化 |

## 更新日志

详见 [CHANGELOG.md](./CHANGELOG.md)。

## License

[MIT](./LICENSE)