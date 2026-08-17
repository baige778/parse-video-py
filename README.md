   * [支持平台](#支持平台)
   * [安装](#安装)
   * [Docker](#docker)
   * [依赖模块](#依赖模块)

Python短视频去水印, 视频目前支持25个平台, 图集目前支持5个平台, 欢迎各位Star。
> 💡tips
> 1. 出现解析失败可在 issue 中提问，请提供可用于复现的平台信息、分享链接.
> 2. 使用时, 请尽量使用app分享链接, 电脑网页版未做充分测试.

---

# MCP 支持
本项目现已支持 [MCP (Model Context Protocol)](https://modelcontextprotocol.io/)，提供StreamableHttp方式接入， 接入URL： http://localhost:8000/mcp

# 支持平台
## 图集
| 平台 | 状态 |
|----|----|
| 抖音 | ✔  |
| 快手 | ✔  |
| 小红书 | ✔  |
| 皮皮虾 | ✔  |
| 微博 | ✔  |

## 图集 LivePhoto
| 平台 | 状态 |
|----|----|
| 小红书 | ✔  |
| 抖音 | ✔  |

## 视频
| 平台       | 状态 |
|----------|----|
| 小红书      | ✔  |
| 皮皮虾      | ✔  |
| 抖音短视频    | ✔  |
| 火山短视频    | ✔  |
| 皮皮搞笑     | ✔  |
| 快手短视频    | ✔  |
| 微视短视频    | ✔  |
| 西瓜视频     | ✔  |
| 最右       | ✔  |
| 梨视频      | ✔  |
| 度小视(原全民) | ✔  |
| 逗拍       | ✔  |
| 微博       | ✔  |
| 绿洲       | ✔  |
| 全民K歌     | ✔  |
| 6间房      | ✔  |
| 美拍       | ✔  |
| 新片场      | ✔  |
| 好看视频     | ✔  |
| 虎牙       | ✔  |
| AcFun    | ✔  |
| 央视网     | ✔  |
| 搜狐视频    | ✔  |
| 哔哩哔哩	| ✔  |
| 腾讯视频    | ✔  |
| Twitter/X	| ✔  |

# 运行

## 本地运行

### 使用 uv（推荐）
```shell
# 进入项目根目录
cd parse-video-py

# 创建虚拟环境并安装全部依赖
uv venv && uv pip install -e ".[all]"

# 激活虚拟环境
source .venv/bin/activate
```

### CLI 命令行
```shell
# 安装
uv pip install -e ".[all]"

# 解析视频
parse-video-py parse "https://v.douyin.com/xxx"
parse-video-py parse "https://v.douyin.com/xxx" --format json

# 启动 Web 服务
parse-video-py serve --port 8000

# 查看版本
parse-video-py version
```

### 抖音解析说明（风控兜底）

抖音网页端已启用 JS 风控（a_bogus / __ac_signature / 浏览器指纹），原生分享页解析会失效。
本仓库已集成两条自动兜底路径：

1. **登录 Cookie 纯 HTTP**：把登录抖音后的 Cookie 保存为 `douyin_cookies.txt`
   （支持原始 Cookie 字符串 / Netscape 格式 / JSON），或通过环境变量
   `PARSE_VIDEO_PY_DOUYIN_COOKIES` 指定路径。解析时自动直连详情接口，无需签名。
2. **无头浏览器**：没有 Cookie 或 Cookie 失效时，自动用 Playwright 驱动本机
   Edge/Chrome 打开视频页抓取数据。

安装抖音兜底依赖：

```shell
uv pip install -e ".[douyin]"   # 包含 curl_cffi + playwright
```

Cookie 获取：浏览器登录 douyin.com 后，F12 → Network → 任意 douyin.com 请求 →
复制 Cookie 头，粘贴到 `douyin_cookies.txt`。

> 注意：Cookie 属于登录凭证，请勿提交到 git（.gitignore 已忽略）。

### 如需开启basic auth认证，请自行设置环境变量，不设置不开启，默认不开启
```shell
export PARSE_VIDEO_USERNAME=username
export PARSE_VIDEO_PASSWORD=password
```

### 如需设置代理，请设置环境变量（不设置则直连）
```shell
# 无认证代理
export PARSE_VIDEO_PROXY=http://proxy.example.com:端口

# 有认证代理
export PARSE_VIDEO_PROXY=http://user:pass@proxy.example.com:端口
```

### 运行app
```shell
uvicorn parse_video_py.web:app --reload
```

## Docker运行
### 获取 docker image
```bash
docker pull baige778/parse-video-py
```

### 运行 docker 容器, 端口 8000
```bash
docker run -d -p 8000:8000 --name parse-video baige778/parse-video-py
```

### Docker Compose 一键部署（推荐，新机器只需要这一节）

仓库自带 `docker-compose.yml` 和 `Dockerfile`（已集成抖音兜底：
登录 Cookie 纯 HTTP 直连；默认不安装无头浏览器，避免构建时 apt 网络问题）。

#### 新机器部署步骤

1. 拉取代码（`dev` 分支为功能最新分支）：

```bash
git clone -b dev https://github.com/baige778/parse-video-py.git
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

返回 `{"code":200,...}` 即正常。浏览器访问 `http://127.0.0.1:8000/` 可看前端页面。

#### 对接 AstrBot 插件

配套插件 `astrbot_plugin_video_parser` 配置 `parser_api_base_url`：

- 插件与解析服务在同一台机器：`http://127.0.0.1:8000`
- 插件在其他设备：`http://<解析服务所在机器IP>:8000`

插件会把抖音视频下载到本地后以文件方式发送（已处理 CDN 防盗链），
B 站等其他平台直发视频 URL。

#### 更新部署

```bash
git pull origin dev
docker compose up -d --build
```

#### 开发与推送说明

- 新功能在 `dev` 分支开发，验证稳定后合并到 `main`；
- 提交并推送（国内网络需要代理，例如本机 Clash `127.0.0.1:7890`）：

```bash
git add -A
git commit -m "功能说明"
git push origin dev
```

- 功能稳定后合并到 `main`：

```bash
git checkout main
git pull origin main
git merge dev
git push origin main
```

#### 常见问题

- 容器内解析报 `httpx.ConnectError` / SSL 错误而宿主机正常：WSL2 网络问题，
  将 WSL 虚拟网卡 MTU 改为 1350 并重启 Docker Desktop，或关闭 Clash 的 TUN 模式；
- 抖音报“Cookie 直连失败且禁用浏览器”：`data/douyin_cookies.txt` 缺失或 Cookie 失效，
  刷新 Cookie 后执行 `docker compose restart`；
- 抖音视频超过 50MB 不发送：插件侧 `video_max_size_mb` 限制，在插件配置中调大即可。

### 运行docker容器，开启basic auth认证
```bash
docker run -d -p 8000:8000 --name parse-video -e PARSE_VIDEO_USERNAME=username -e PARSE_VIDEO_PASSWORD=password baige778/parse-video-py
```

### 运行docker容器，设置代理
```bash
docker run -d -p 8000:8000 --name parse-video -e PARSE_VIDEO_PROXY=http://proxy.example.com:端口 baige778/parse-video-py
```

# 查看前端页面
访问: http://127.0.0.1:8000/

请求接口, 查看json返回
```bash
curl 'http://127.0.0.1:8000/video/share/url/parse?url=视频分享链接' | jq
```
返回格式
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
| author.uid | 视频作者id |
| author.name | 视频作者名称 |
| author.avatar | 视频作者头像 |
| title | 视频标题 |
| video_url | 视频无水印链接 |
| music_url | 视频音乐链接 |
| cover_url | 视频封面 |
| images | 图集图片列表 |
| images.[index].url | 图集图片地址 |
| images.[index].live_photo_url | 图集图片 livephoto 视频地址 |
> 字段除了视频地址, 其他字段可能为空

# 自己写方法调用
```python
import json
import asyncio

from parse_video_py import parse_video_share_url, parse_video_id, VideoSource

# 根据分享链接解析
video_info = asyncio.run(parse_video_share_url("分享链接"))
print(
    "解析分享链接：\n",
    json.dumps(video_info, ensure_ascii=False, indent=4, default=lambda x: x.__dict__),
    "\n",
)

# 根据视频id解析
video_info = asyncio.run(
    parse_video_id(VideoSource.DouYin, "视频ID")
)
print(
    "解析视频ID：\n",
    json.dumps(video_info, ensure_ascii=False, indent=4, default=lambda x: x.__dict__),
    "\n",
)
```


# 依赖模块
| 模块        | 作用                                   |
|-------------|--------------------------------------|
| fastapi     | web框架                                |
| fastapi-mcp | 支持MCP                                |
| httpx       | HTTP 和 REST 客户端                      |
| parsel      | 解析html页面                             |
| pre-commit  | 对git代码提交前进行检查，结合flake8，isort，black使用 |
| flake8      | 工程化：代码风格一致性                          |
| isort       | 工程化：格式化导入package                     |
| black       | 工程化：代码格式化                            |
