from __future__ import annotations

from typing import TypeVar

from fastapi import Header, Request

from app.core.errors import BusinessError, ErrorCode
from app.services.auth.auth_service import AuthService

T = TypeVar("T")


def require_app_state_service(request: Request, attr: str, label: str) -> T:
    """从 app.state 读取 lifespan 注入的服务；未初始化时抛出明确错误。"""
    service = getattr(request.app.state, attr, None)
    if service is None:
        raise RuntimeError(f"{label} is not initialized")
    return service


def get_auth_service(request: Request) -> AuthService:
    return require_app_state_service(request, "auth_service", "AuthService")


def extract_bearer_token(authorization: str | None = Header(default=None)) -> str:
    if not authorization:
        raise BusinessError(ErrorCode.AUTH_TOKEN_MISSING)
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        raise BusinessError(ErrorCode.AUTH_TOKEN_INVALID)
    token = authorization[len(prefix) :].strip()
    if not token:
        raise BusinessError(ErrorCode.AUTH_TOKEN_INVALID)
    return token
