from __future__ import annotations

from dataclasses import dataclass


DEFAULT_DEV_PERMISSIONS: list[str] = [
    "chat:read",
    "chat:send",
    "chat:delete",
    "folder:read",
    "folder:write",
    "template:read",
    "template:write",
    "kb:read",
    "kb:create",
    "kb:update",
    "kb:delete",
    "kb:file:read",
    "kb:file:upload",
    "kb:file:delete",
    "kb:import",
    "kb:hit_test",
]


@dataclass(frozen=True, slots=True)
class DevUser:
    id: str
    email: str
    password: str
    display_name: str
    permissions: list[str]
    team_id: str


# 仅开发环境：明文密码与内存用户表，禁止用于生产。
_DEV_USERS: tuple[DevUser, ...] = (
    DevUser(
        id="user_admin",
        email="admin@example.com",
        password="admin123",
        display_name="Admin User",
        permissions=list(DEFAULT_DEV_PERMISSIONS),
        team_id="team_default",
    ),
    DevUser(
        id="user_demo",
        email="demo@example.com",
        password="demo123",
        display_name="Demo User",
        permissions=list(DEFAULT_DEV_PERMISSIONS),
        team_id="team_default",
    ),
)


def find_dev_user_by_email(email: str) -> DevUser | None:
    normalized = email.strip().lower()
    for user in _DEV_USERS:
        if user.email.lower() == normalized:
            return user
    return None


def find_dev_user_by_id(user_id: str) -> DevUser | None:
    for user in _DEV_USERS:
        if user.id == user_id:
            return user
    return None


def verify_dev_credentials(email: str, password: str) -> DevUser | None:
    user = find_dev_user_by_email(email)
    if user is None or user.password != password:
        return None
    return user
