from fastapi import HTTPException, status

MANAGED_CHAT_KEYS = frozenset({
    "use_multimodal_kb", "multimodal_kb_id", "multimodal_file_id",
    "use_graph", "db_id", "selectedKB",
})

def assert_chat_features_allowed(user, meta: dict | None) -> None:
    meta = meta or {}
    requested = any(meta.get(key) not in (None, False, "") for key in MANAGED_CHAT_KEYS)
    if requested and user.role != "superadmin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="当前角色只能使用普通知识问答")

def can_manage_target(actor, target) -> bool:
    if actor.role == "superadmin":
        return True
    return actor.role == "admin" and target.role == "user"
