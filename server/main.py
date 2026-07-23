import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from dotenv import load_dotenv

from fastapi import FastAPI, Request, HTTPException, status, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from server.routers import router
from server.services.http_clients import (
    close_multimodal_client,
    close_graph_worker_client,
    close_tianshu_client,
)
from server.utils.auth_middleware import is_public_path
from src import shutdown_runtime
from src.utils.logging_config import logger

# 加载环境变量
env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'src', '.env')
load_dotenv(env_path)
logger.info(f"加载环境变量文件: {env_path}")


@asynccontextmanager
async def app_lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Manage application startup and shutdown."""
    try:
        yield
    finally:
        try:
            await close_multimodal_client()
        finally:
            try:
                await close_graph_worker_client()
            finally:
                try:
                    await close_tianshu_client()
                finally:
                    shutdown_runtime()


app = FastAPI(lifespan=app_lifespan)
app.include_router(router, prefix="/api")

# CORS 设置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 鉴权中间件
class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # 获取请求路径
        path = request.url.path

        # 检查是否为公开路径，公开路径无需身份验证
        if is_public_path(path):
            return await call_next(request)

        if not path.startswith("/api"):
            # 非API路径，可能是前端路由或静态资源
            return await call_next(request)

        # # 提取Authorization头
        # auth_header = request.headers.get("Authorization")
        # if not auth_header or not auth_header.startswith("Bearer "):
        #     return JSONResponse(
        #         status_code=status.HTTP_401_UNAUTHORIZED,
        #         content={"detail": f"请先登录。Path: {path}"},
        #         headers={"WWW-Authenticate": "Bearer"}
        #     )

        # # 获取token
        # token = auth_header.split("Bearer ")[1]

        # # 添加token到请求状态，后续路由可以直接使用
        # request.state.token = token

        # 继续处理请求
        return await call_next(request)

# 添加鉴权中间件
#app.add_middleware(AuthMiddleware)
