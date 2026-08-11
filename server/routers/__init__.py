from fastapi import APIRouter
from server.routers.chat_router import chat
from server.routers.data_router import data
from server.routers.base_router import base
from server.routers.auth_router import auth
from server.routers.college_router import college
from server.routers.multimodal_proxy_router import multimodal
from server.routers.statistics_router import router as statistics_router
from server.routers.user_model_router import user_models
from server.routers.feedback_router import router as feedback_router
from server.routers.governance_router import router as governance_router
from server.routers.evaluation_router import router as evaluation_router

router = APIRouter()
router.include_router(base)
router.include_router(chat)
router.include_router(data)
router.include_router(auth)
router.include_router(college)
router.include_router(multimodal)
router.include_router(statistics_router)
router.include_router(user_models)
router.include_router(feedback_router)
router.include_router(governance_router)
router.include_router(evaluation_router)
