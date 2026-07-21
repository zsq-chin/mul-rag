import logging
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy.orm import Session

from server.models.user_model import User
from server.services.model_credentials import (
    CredentialCipher,
    create_user_model,
    delete_user_model,
    list_user_models,
    select_user_model,
    serialize_user_model,
    update_user_model,
    validate_api_base,
)
from server.utils.auth_middleware import get_db, get_required_user


logger = logging.getLogger(__name__)
user_models = APIRouter(prefix="/chat/user-models", tags=["user-models"])


class UserModelCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=100)
    provider: Literal["openai-compatible"] = "openai-compatible"
    model_name: str = Field(min_length=1, max_length=200)
    api_base: str = Field(min_length=1, max_length=500)
    api_key: SecretStr


class UserModelUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    provider: Literal["openai-compatible"] | None = None
    model_name: str | None = Field(default=None, min_length=1, max_length=200)
    api_base: str | None = Field(default=None, min_length=1, max_length=500)
    api_key: SecretStr | None = None


class UserModelValidate(BaseModel):
    api_base: str = Field(min_length=1, max_length=500)
    api_key: SecretStr


class UserModelResponse(BaseModel):
    id: int
    display_name: str
    provider: str
    model_name: str
    api_base: str
    key_hint: str
    has_api_key: bool
    last_used_at: str | None
    created_at: str | None
    updated_at: str | None


def _cipher_or_503() -> CredentialCipher:
    try:
        return CredentialCipher()
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="模型凭据加密服务未配置",
        ) from None


@user_models.get("", response_model=list[UserModelResponse])
async def get_user_models(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_required_user),
):
    return list_user_models(db, current_user)


@user_models.post("", response_model=UserModelResponse, status_code=status.HTTP_201_CREATED)
async def add_user_model(
    payload: UserModelCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_required_user),
):
    try:
        model = create_user_model(db, current_user, payload, _cipher_or_503())
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from None
    return serialize_user_model(model)


@user_models.patch("/{model_id}", response_model=UserModelResponse)
async def edit_user_model(
    model_id: int,
    payload: UserModelUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_required_user),
):
    try:
        model = update_user_model(db, current_user, model_id, payload, _cipher_or_503())
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from None
    return serialize_user_model(model)


@user_models.delete("/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_user_model(
    model_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_required_user),
):
    delete_user_model(db, current_user, model_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@user_models.post("/{model_id}/select", response_model=UserModelResponse)
async def mark_user_model_selected(
    model_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_required_user),
):
    return serialize_user_model(select_user_model(db, current_user, model_id))


@user_models.post("/validate")
async def validate_user_model(
    payload: UserModelValidate,
    current_user: User = Depends(get_required_user),
):
    del current_user
    try:
        api_base = validate_api_base(payload.api_base)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from None

    headers = {"Authorization": f"Bearer {payload.api_key.get_secret_value()}"}
    timeout = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.get(f"{api_base}/models", headers=headers)
        if response.status_code < 200 or response.status_code >= 300:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"模型验证失败（HTTP {response.status_code}）",
            )
    except HTTPException:
        raise
    except httpx.HTTPError as error:
        logger.warning("User model validation failed: %s", type(error).__name__)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无法连接模型服务") from None

    return {"valid": True}
