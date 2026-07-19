# docker/graphrag.Dockerfile
FROM python:3.12-slim

WORKDIR /app

# 安装依赖
RUN pip install --no-cache-dir graphrag==0.1.1 fastapi uvicorn

# 拷贝 API 代码到容器
COPY graphrag_api/ /app/graphrag_api

# 设置工作目录和命令为启动 FastAPI
WORKDIR /app

# 默认启动 FastAPI
CMD ["uvicorn", "graphrag_api.main:app", "--host", "0.0.0.0", "--port", "8111"]

