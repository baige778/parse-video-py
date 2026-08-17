FROM python:3.10-slim

# 安装 uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 国内网络构建加速（可选）：
#   docker build --build-arg APT_MIRROR=https://mirrors.tuna.tsinghua.edu.cn \
#     --build-arg PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright
ARG APT_MIRROR=""
ARG PLAYWRIGHT_DOWNLOAD_HOST=""
# 是否在镜像内安装无头浏览器（默认关闭：需要 apt 安装系统依赖，国内网络容易失败）。
# 需要时构建加参数：--build-arg INSTALL_BROWSER=1
ARG INSTALL_BROWSER=0
ENV PLAYWRIGHT_DOWNLOAD_HOST=${PLAYWRIGHT_DOWNLOAD_HOST}
ENV DEBIAN_FRONTEND=noninteractive

# 设置工作目录
WORKDIR /app

# 复制项目文件（templates 已在 src/parse_video_py/templates/ 中）
COPY pyproject.toml .
COPY src/ src/

# 使用 uv 安装依赖（Web/CLI + 抖音兜底：curl_cffi、playwright）
RUN uv pip install --system ".[all,douyin]" \
    && if [ "$INSTALL_BROWSER" = "1" ]; then \
         if [ -n "$APT_MIRROR" ]; then \
         MIRROR_HOST=$(echo "$APT_MIRROR" | sed 's|^https\?://||; s|/$||'); \
         sed -i "s|deb.debian.org|$MIRROR_HOST|g" /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
         sed -i "s|deb.debian.org|$MIRROR_HOST|g" /etc/apt/sources.list; \
         sed -i "s|http://$MIRROR_HOST|https://$MIRROR_HOST|g" /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
         sed -i "s|http://$MIRROR_HOST|https://$MIRROR_HOST|g" /etc/apt/sources.list; \
         fi \
         && apt-get update \
         && python -m playwright install --with-deps chromium; \
       fi

# 暴露端口
EXPOSE 8000

# 启动 FastAPI 应用
CMD ["uvicorn", "parse_video_py.web:app", "--host", "0.0.0.0", "--port", "8000"]
