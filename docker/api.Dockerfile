FROM python:3.12
COPY --from=ghcr.io/astral-sh/uv:0.7.2 /uv /uvx /bin/

WORKDIR /app

ARG http_proxy
ARG https_proxy
ENV http_proxy=$http_proxy \
    https_proxy=$https_proxy \
    TZ=Asia/Shanghai \
    UV_LINK_MODE=copy

RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

RUN apt-get update && apt-get install -y \
    python3-dev \
    ffmpeg \
    libsm6 \
    libxext6

# 先复制依赖描述文件
COPY ../pyproject.toml /app/pyproject.toml
COPY ../uv.lock /app/uv.lock
COPY ../.python-version /app/.python-version

# 安装依赖
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync

# 再复制项目代码
COPY ../src /app/src
COPY ../server /app/server

# （后续启动命令）
CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000"]
