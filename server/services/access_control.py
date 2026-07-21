from fastapi import HTTPException, status

VALID_ROLES = frozenset({"superadmin", "admin", "user"})

SUPERADMIN_CHAT_KEYS = frozenset({"use_graph"})

def assert_chat_features_allowed(user, meta: dict | None) -> None:
    meta = meta or {}
    requested = any(meta.get(key) not in (None, False, "") for key in SUPERADMIN_CHAT_KEYS)
    if requested and user.role != "superadmin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="当前角色只能使用普通知识问答")

def can_manage_target(actor, target) -> bool:
    if actor.role == "superadmin":
        return True
    return actor.role == "admin" and target.role == "user"

def assert_role_assignment_allowed(actor, requested_role: str) -> str:
    if requested_role not in VALID_ROLES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无效的角色")
    if actor.role == "admin" and requested_role != "user":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="管理员只能创建普通用户账户")
    return requested_role


def assert_superadmin_transition_allowed(target, requested_role: str, superadmin_count: int | None) -> None:
    is_demotion = target.role == "superadmin" and requested_role != "superadmin"
    if is_demotion and (superadmin_count is None or superadmin_count <= 1):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不能降级最后一个超级管理员账户",
        )
