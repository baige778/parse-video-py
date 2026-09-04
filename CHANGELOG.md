# Changelog

所有重要变更均会记录在此文件中。

## [v0.0.11] - 2026-09-05

### 修复

- **登录态 Cookie 键扩展**：抖音网页版会话 Cookie 已由 `sessionid` 迁移为 `sid_tt`/`sid_guard`/`uid_tt` 等（旧 `sessionid` 会被服务端清除），扩展登录 Cookie 识别集合，修复有效登录态被误判「抖音登录态已失效」、以及扫码成功后轮询检测不到登录态的问题
- **登录态判定改为 Cookie 快判 + 首页 DOM 校验**：无任何登录 Cookie 直接判未登录（无需加载页面）；有 Cookie 时以首页「退出登录」/可见「登录」入口校验，避免会话被服务端作废时误判已登录
- **已删除视频明确报错**：detail 接口正常响应但无 `aweme_detail` 时抛出「视频不存在或已删除」，插件可走已删除视频友好提示，不再误报「登录态失效/验证码」

### 优化改进

- **浏览器兜底解析抗临时风控**：登录态有效但未捕获 detail 接口时自动重载页面重试一次，降低风控/验证码临时拦截导致的解析失败

---

## [v0.0.10] - 2026-09-03

### 优化改进

- **全平台解析结果缓存**：新增进程内 TTL 结果缓存（默认 300s，可通过 `PARSE_VIDEO_PY_CACHE_TTL` 配置），降低重复解析耗时
- **HTTP 连接池复用**：改用全局共享 `httpx.AsyncClient` 复用连接池（`max_keepalive_connections=20`），避免每请求重复 TCP/TLS 握手；流式下载改走独立专职客户端
- **fake-useragent 单例缓存**：`random_user_agent` 结果缓存，避免每次请求重新初始化 / 读盘

### 修复

- **登录态判定改用首页 DOM**：`profile/self` 接口在无头 + 风控环境下会误报 `status_code=8`（用户未登录），改为校验首页右上角「退出登录」菜单项判定登录态，修复插件误显示「未登录」
- **解析前首页预热**：打开视频页前先访问抖音首页，让 sec_sdk 完成初始化并刷新 `ttwid`/`msToken` 风控 Cookie，避免 detail 接口被风控静默拦截

---

## [v0.0.7] - 2026-09-03

### 新增功能

- **持久化登录态浏览器（方案A）**：移除 Cookie 文件直连（抖音 ArgusSecurity 返回 "Uifid Not Found" 已失效），改为磁盘持久化 `user_data_dir` profile 的常驻 Chromium，长期持有并滚动刷新登录态 / `ttwid` / `UIFID` 等设备指纹
- 解析与扫码登录共用同一 profile：扫码登录一次后，浏览器 profile 持久化登录态，后续解析直接复用，无需导出 Cookie

### 优化改进

- 浏览器状态改用异步 `asyncio` 统一管理，新增浏览器锁超时（默认 20s）与卡死强制回收，避免单请求卡死拖垮后续解析
- 修复浏览器接口轮询超时单位 bug（毫秒被误当秒），轮询超时对齐为 8s
- 清理已废弃的 Cookie 直连环境变量（`PARSE_VIDEO_PY_DOUYIN_COOKIES` / `_COOKIE_TIMEOUT` / `_COOKIE_RETRIES`），并移除 `curl_cffi` 依赖
- 新增 `PARSE_VIDEO_PY_DOUYIN_PROFILE_DIR` 配置浏览器 profile 目录

---

## [v0.0.5] - 2026-09-02

### 优化改进

- **浏览器常驻复用**：headless 模式复用单例 Chromium，避免每请求 1~3s 冷启动；按 TTL / 使用次数自动回收，可通过 `PARSE_VIDEO_PY_DOUYIN_BROWSER_REUSE*` 配置
- **video_id 级结果缓存**：新增进程内 TTL 结果缓存（默认 300s），降低重复解析与 403 频率，可通过 `PARSE_VIDEO_PY_DOUYIN_CACHE_TTL` 配置
- **video_id 复用**：抖音原生解析失败后，兜底路径直接复用已解析的 video_id，避免重复请求短链

---

## [v0.0.4] - 2026-08-30

### 优化改进

- **移除假 `a_bogus` 签名请求**：图集解析不再请求需要真实签名的 `slidesinfo` 接口（假签名必然 403），改为直接解析 HTML
- **Cookie 直连提速**：超时从 25s 压到 5s，失败自动重试（默认 1 次，3~5s 退避）
- **浏览器兜底超时对齐**：goto 15s、轮询 10s，缩短失败路径耗时
- **结构化日志**：为 native / Cookie / browser 全路径添加耗时埋点日志（输出 stderr）
- **全参数环境变量化**：超时 / 重试 / 浏览器通道等均支持 `PARSE_VIDEO_PY_DOUYIN_*` 配置

---

## [v0.0.3] - 2026-04-19

### 新增功能

- **新增 CLI 命令行工具**：支持 `version`/`parse`/`serve` 三个子命令，可通过 `parse-video-py` 入口直接使用
- **新增 CLI `-h` 简写**：所有命令支持 `-h` 作为 `--help` 的简写
- **新增 pyproject.toml**：使用 hatchling 构建，支持 `[web]`/`[cli]`/`[dev]` 可选依赖安装
- **新增包公开 API**：支持 `from parse_video_py import VideoSource, parse_video_share_url` 直接调用

### 架构重构

- **迁移到 src 标准布局**：`parser/`、`utils/`、`templates/` 统一迁移到 `src/parse_video_py/` 下
- **uv 包管理**：从 venv + requirements.txt 迁移到 uv + pyproject.toml，支持 `uv pip install -e ".[all]"`
- **Web 服务拆分**：从 `main.py` 提取到 `src/parse_video_py/web.py`，`main.py` 改为薄入口
- **URL 工具统一**：`URL_REG` 正则和 `extract_url` 提取到 `utils.py`，消除 web/cli 模块间的重复定义

### 优化改进

- **Web 序列化**：使用 `dataclasses.asdict()` 替代 `__dict__`，正确处理嵌套 dataclass 序列化
- **Auth 依赖缓存**：Basic Auth 依赖在模块加载时构建一次，避免每个路由重复调用
- **批量解析并发限制**：CLI 批量解析添加 `Semaphore(10)` 防止无界并发
- **Dockerfile 更新**：使用 uv 安装依赖，适配 src 布局
- **CI 更新**：GitHub Actions 改用 `astral-sh/setup-uv`

---

## [v0.0.2] - 2026-04-18

### 新增功能

- **新增 B站(哔哩哔哩) 视频解析**：支持 bilibili.com、b23.tv、m.bilibili.com 域名
- **新增 Twitter/X 视频解析**：支持 twitter.com、x.com、t.co 域名
- **新增微博图集解析**：支持微博图片帖子的图集批量提取
- **新增抖音 Live Photo 实况照片支持**：通过 slidesinfo API 提取实况照片视频
- **新增图集批量下载功能**：前端支持图集图片批量下载 (#58)
- **新增 MCP 支持**：通过 StreamableHttp 方式接入 MCP 协议，接入 URL: `/mcp`
- **新增主题样式选择**：前端页面支持多种主题风格切换
- **新增 Basic Auth 自定义认证**：支持通过环境变量自定义用户名密码 (#48)
- **新增 Claude Code 集成**：添加 CLAUDE.md 项目指引和 GitHub Actions CI 工作流

### 优化改进

- **小红书图集图片高清化**：图集图片使用高清地址，优化图片域名替换逻辑 (#45)
- **抖音图集解析音频**：支持抖音图集内容的音频提取 (#70)
- **单元测试覆盖**：添加核心模块单元测试，pre-commit 支持提交时自动运行测试
- **分享链接正则优化**：优化 URL 匹配正则表达式，增强无效输入处理鲁棒性 (#74)
- **依赖管理优化**：整理 requirements.txt 依赖项

### Bug 修复

- **修复无效分享链接导致崩溃**：无效 URL 输入不再导致服务异常
- **修复小红书图片域名替换逻辑**：当图片 URL 不包含 notes_pre_post 时使用原域名

---

## [v0.0.1] - 初始版本

### 基础功能

- 支持 20+ 平台视频去水印解析
- 支持 4 平台图集解析（抖音、快手、小红书、皮皮虾）
- 支持 LivePhoto 解析（小红书）
- FastAPI Web 服务 + REST API 接口
- 前端解析页面
- Docker 部署支持
