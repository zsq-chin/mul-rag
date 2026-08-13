import asyncio
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from dotenv import load_dotenv

from fastapi import FastAPI, Request, HTTPException, status, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from server.routers import router
from server.db_manager import db_manager
from server.services import alert_service
from server.services.http_clients import (
    close_multimodal_client,
    close_graph_worker_client,
    close_tianshu_client,
)
from server.utils.auth_middleware import is_public_path
from server.utils import multimodal_remote
from server.utils.cors_config import resolve_cors_config
from src import config, shutdown_runtime
from src.utils.logging_config import logger

# 加载环境变量
env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'src', '.env')
load_dotenv(env_path)
logger.info(f"加载环境变量文件: {env_path}")


async def _run_alert_checker(stop_event: asyncio.Event) -> None:
    """后台告警检查任务：循环评估告警规则，stop_event 置位后等待当前轮退出。

    评估全程经 alert_loop 的 asyncio.to_thread 放到后台线程，阻塞探测不占用事件循环。
    间隔与单轮超时均须为正数，非法环境变量由 alert_service 兜底回退默认值。
    """
    def _evaluate() -> None:
        session = None
        try:
            session = db_manager.get_session()
            ctx = {
                "db_path": db_manager.db_path,
                "save_dir": config.save_dir,
                "milvus_uri": os.getenv("MILVUS_URI", config.get("milvus_uri", "http://milvus:19530")),
                "neo4j_uri": os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
                "neo4j_username": os.environ.get("NEO4J_USERNAME"),
                "neo4j_password": os.environ.get("NEO4J_PASSWORD"),
            }

            def _notify(rule, subject, body, is_resolve):
                # SMTP 发送失败仅降级，绝不影响监控主流程，也绝不输出密码
                try:
                    to = rule.notify_email
                    if not to:
                        return
                    alert_service.send_email(alert_service.smtp_from_env(), to, subject, body)
                except alert_service.AlertError:
                    pass

            alert_service.evaluate_rules(session, ctx, notify=_notify)
        finally:
            if session is not None:
                session.close()

    interval = alert_service.alert_interval_seconds()
    round_timeout = alert_service.alert_round_timeout_seconds()
    await alert_service.alert_loop(
        _evaluate, interval=interval, round_timeout=round_timeout, stop=stop_event
    )


@asynccontextmanager
async def app_lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Manage application startup and shutdown."""
    alert_stop = asyncio.Event()
    alert_task = asyncio.create_task(_run_alert_checker(alert_stop))
    # I1.5：启动日志只打印「多模态已启用/模式/目标主机脱敏标识」，绝不打印秘密
    _multimodal_base = multimodal_remote.get_multimodal_api_base()
    if _multimodal_base:
        logger.info(
            "多模态已启用 mode=%s target=%s",
            multimodal_remote.get_multimodal_mode(),
            multimodal_remote.sanitize_base_url_for_log(_multimodal_base),
        )
    elif multimodal_remote.is_multimodal_enabled():
        logger.warning(
            "MULTIMODAL_ENABLED 已开启但未配置有效目标（mode=%s），多模态实际未启用",
            multimodal_remote.get_multimodal_mode(),
        )
    else:
        logger.info("多模态未启用（MULTIMODAL_ENABLED=false）")
    try:
        yield
    finally:
        # 关闭应用：等待告警检查任务退出（限时，超时则取消）
        alert_stop.set()
        if alert_task:
            try:
                await asyncio.wait_for(alert_task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                alert_task.cancel()
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
                    # 9.1.4：应用 shutdown 释放全部 SQLite 连接，消除 unclosed connection 警告
                    db_manager.close()


app = FastAPI(lifespan=app_lifespan)
app.include_router(router, prefix="/api")

# CORS 设置：使用环境变量中的明确前端来源，不再 allow_origins=["*"] 加 credentials（9.3.1）。
_cors_origins, _cors_allow_credentials = resolve_cors_config(
    os.getenv("CORS_ALLOWED_ORIGINS", "")
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_allow_credentials,
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
