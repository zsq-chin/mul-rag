"""知识字典角色权限（设计文档 §3）。

- `admin` 与 `superadmin` 权限完全相同（可管理）；
- `user` 只能查看、检索已发布活动版本；
- 后端对每个接口独立鉴权，前端权限只改善用户体验。
"""

from __future__ import annotations

from typing import Any

from .errors import Forbidden

MANAGER_ROLES = frozenset({"admin", "superadmin"})


def is_manager(user: Any) -> bool:
    """用户是否具备管理能力（admin 或 superadmin）。"""
    role = getattr(user, "role", None)
    return role in MANAGER_ROLES


def ensure_manager(user: Any) -> None:
    """管理操作门卫：非 admin/superadmin 一律 403。"""
    if not is_manager(user):
        raise Forbidden("当前角色无权执行该操作，需要管理员或超级管理员权限")


def ensure_can_read_version(user: Any, version_status: str, dictionary_status: str, is_active: bool) -> None:
    """普通用户只能读取已发布且为活动版本的内容（§3 / §10.4）。"""
    if is_manager(user):
        return
    if dictionary_status != "published" or version_status != "published" or not is_active:
        raise Forbidden("普通用户只能查看已发布的活动版本")
