import ipaddress
import os
import socket
from datetime import datetime, timezone
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError

from server.models.user_model_credential import UserModelCredential


class CredentialCipher:
    def __init__(self, key: str | None = None):
        raw_key = key or os.getenv("MODEL_CREDENTIAL_MASTER_KEY")
        if not raw_key:
            raise ValueError("缺少 MODEL_CREDENTIAL_MASTER_KEY")
        try:
            self._fernet = Fernet(raw_key.encode("ascii"))
        except (ValueError, UnicodeError) as error:
            raise ValueError("MODEL_CREDENTIAL_MASTER_KEY 格式无效") from None

    def encrypt(self, value: str) -> str:
        if not value:
            raise ValueError("API Key 不能为空")
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeError, ValueError):
            raise ValueError("模型凭据无法解密") from None


def _allowed_hosts() -> set[str]:
    return {
        item.strip().lower()
        for item in os.getenv("USER_MODEL_ALLOWED_HOSTS", "").split(",")
        if item.strip()
    }


def validate_api_base(value: str) -> str:
    normalized = (value or "").strip().rstrip("/")
    parsed = urlsplit(normalized)
    allow_http = os.getenv("USER_MODEL_ALLOW_HTTP", "false").lower() == "true"
    allowed_schemes = {"https", "http"} if allow_http else {"https"}

    if parsed.scheme.lower() not in allowed_schemes or not parsed.hostname:
        raise ValueError("模型地址必须是有效的 HTTPS URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("模型地址不能包含凭据、查询参数或片段")

    hostname = parsed.hostname.lower()
    if hostname in _allowed_hosts():
        return normalized

    try:
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        addresses = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except (OSError, ValueError):
        raise ValueError("模型地址无法解析") from None

    if not addresses:
        raise ValueError("模型地址无法解析")

    for address in addresses:
        try:
            ip = ipaddress.ip_address(address[4][0])
        except ValueError:
            raise ValueError("模型地址解析结果无效") from None
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise ValueError("该模型地址不允许访问")

    return normalized


def _secret_value(value) -> str:
    if hasattr(value, "get_secret_value"):
        return value.get_secret_value()
    return str(value or "")


def serialize_user_model(model: UserModelCredential) -> dict:
    return {
        "id": model.id,
        "display_name": model.display_name,
        "provider": model.provider,
        "model_name": model.model_name,
        "api_base": model.api_base,
        "key_hint": model.key_hint,
        "has_api_key": bool(model.encrypted_api_key),
        "last_used_at": model.last_used_at.isoformat() if model.last_used_at else None,
        "created_at": model.created_at.isoformat() if model.created_at else None,
        "updated_at": model.updated_at.isoformat() if model.updated_at else None,
    }


def list_user_models(db, user) -> list[dict]:
    models = (
        db.query(UserModelCredential)
        .filter(UserModelCredential.user_id == user.id)
        .order_by(UserModelCredential.last_used_at.desc(), UserModelCredential.updated_at.desc())
        .all()
    )
    return [serialize_user_model(model) for model in models]


def get_owned_model(db, user, model_id: int) -> UserModelCredential:
    model = (
        db.query(UserModelCredential)
        .filter(
            UserModelCredential.id == model_id,
            UserModelCredential.user_id == user.id,
        )
        .first()
    )
    if model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模型不存在")
    return model


def create_user_model(db, user, payload, cipher: CredentialCipher | None = None) -> UserModelCredential:
    cipher = cipher or CredentialCipher()
    api_key = _secret_value(payload.api_key)
    model = UserModelCredential(
        user_id=user.id,
        display_name=payload.display_name.strip(),
        provider=payload.provider,
        model_name=payload.model_name.strip(),
        api_base=validate_api_base(payload.api_base),
        encrypted_api_key=cipher.encrypt(api_key),
        key_hint=api_key[-4:],
    )
    db.add(model)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="模型名称已存在") from None
    db.refresh(model)
    return model


def update_user_model(db, user, model_id: int, payload, cipher: CredentialCipher | None = None) -> UserModelCredential:
    model = get_owned_model(db, user, model_id)
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)

    if "display_name" in changes:
        model.display_name = changes["display_name"].strip()
    if "provider" in changes:
        model.provider = changes["provider"]
    if "model_name" in changes:
        model.model_name = changes["model_name"].strip()
    if "api_base" in changes:
        model.api_base = validate_api_base(changes["api_base"])
    if "api_key" in changes and changes["api_key"] is not None:
        cipher = cipher or CredentialCipher()
        api_key = _secret_value(changes["api_key"])
        model.encrypted_api_key = cipher.encrypt(api_key)
        model.key_hint = api_key[-4:]

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="模型名称已存在") from None
    db.refresh(model)
    return model


def delete_user_model(db, user, model_id: int) -> None:
    model = get_owned_model(db, user, model_id)
    db.delete(model)
    db.commit()


def select_user_model(db, user, model_id: int) -> UserModelCredential:
    model = get_owned_model(db, user, model_id)
    model.last_used_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(model)
    return model


def resolve_model_for_user(db, user, meta: dict | None, model_selector=None):
    meta = meta or {}
    model_id = meta.get("user_model_id")
    if model_id in (None, ""):
        if model_selector is None:
            from src.models import select_model as model_selector
        return model_selector(
            model_provider=meta.get("model_provider"),
            model_name=meta.get("model_name"),
        )

    try:
        model_id = int(model_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="模型 ID 无效") from None

    credential = get_owned_model(db, user, model_id)
    api_base = validate_api_base(credential.api_base)
    api_key = CredentialCipher().decrypt(credential.encrypted_api_key)
    if model_selector is None:
        from src.models import select_model as model_selector
    return model_selector(custom_model_info={
        "model_name": credential.model_name,
        "api_base": api_base,
        "api_key": api_key,
    })
