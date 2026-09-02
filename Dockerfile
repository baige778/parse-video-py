FROM python:3.10-slim

# 安装 uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 国内网络构建加速（默认启用清华镜像源，海外构建可覆盖为空）。
#   docker build --build-arg APT_MIRROR=https://mirrors.aliyun.com \
#     --build-arg PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright
ARG APT_MIRROR=https://mirrors.tuna.tsinghua.edu.cn
ARG PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright
# 默认安装无头 Chromium（抖音扫码登录依赖），不需要时构建加 --build-arg INSTALL_BROWSER=0
ARG INSTALL_BROWSER=1
ENV PLAYWRIGHT_DOWNLOAD_HOST=${PLAYWRIGHT_DOWNLOAD_HOST} \
    DEBIAN_FRONTEND=noninteractive \
    PIP_DEFAULT_TIMEOUT=100

# 抖音数据目录（二维码 / 浏览器 profile），配合 docker compose 的 ./data:/data 挂载持久化
ENV DOUYIN_DATA_DIR=/data

# 设置工作目录
WORKDIR /app

# 复制项目文件（templates 已在 src/parse_video_py/templates/ 中）
COPY pyproject.toml .
COPY src/ src/

# 安装依赖 + 无头浏览器（含系统依赖，使用国内 apt 镜像源）
RUN uv pip install --system ".[all,douyin]" \
    && if [ "$INSTALL_BROWSER" = "1" ]; then \
         if [ -n "$APT_MIRROR" ]; then \
           MIRROR_HOST=$(echo "$APT_MIRROR" | sed 's|^https\?://||; s|/$||'); \
           for f in /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources; do \
             if [ -f "$f" ]; then \
               sed -i "s|deb.debian.org|$MIRROR_HOST|g; s|security.debian.org|$MIRROR_HOST|g; s|http://$MIRROR_HOST|https://$MIRROR_HOST|g" "$f"; \
             fi; \
           done; \
         fi \
         && apt-get update \
         && python -m playwright install --with-deps chromium; \
       fi \
    && mkdir -p /data

# 暴露端口
EXPOSE 8000

VOLUME ["/data"]

# 启动 FastAPI 应用
CMD ["uvicorn", "parse_video_py.web:app", "--host", "0.0.0.0", "--port", "8000"]
