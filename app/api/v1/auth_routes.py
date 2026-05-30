from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from app.core.dependencies import extract_bearer_token, get_auth_service
from app.core.errors import BusinessError, ErrorCode
from app.core.responses import success_response
from app.schemas.auth import LoginRequest
from app.services.auth.auth_service import AuthService

auth_router = APIRouter(prefix="/v1/auth", tags=["Auth"])


def _set_refresh_cookie(
    response: Response,
    *,
    auth_service: AuthService,
    refresh_token: str,
) -> None:
    response.set_cookie(
        key=auth_service.refresh_cookie_name,
        value=refresh_token,
        httponly=True,
        secure=False,
        samesite="lax",
        path="/",
        max_age=auth_service.refresh_token_ttl_seconds,
    )


def _clear_refresh_cookie(response: Response, *, auth_service: AuthService) -> None:
    response.delete_cookie(
        key=auth_service.refresh_cookie_name,
        httponly=True,
        secure=False,
        samesite="lax",
        path="/",
    )


@auth_router.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    auth_service: AuthService = Depends(get_auth_service),
) -> Any:
    if not body.email.strip() or not body.password:
        raise BusinessError(ErrorCode.AUTH_INVALID_REQUEST)

    login_data, refresh_token = auth_service.login(email=body.email, password=body.password)
    http_response = success_response(login_data.model_dump(by_alias=True, mode="json"), request)
    _set_refresh_cookie(http_response, auth_service=auth_service, refresh_token=refresh_token)
    return http_response


@auth_router.get("/me")
async def me(
    request: Request,
    access_token: str = Depends(extract_bearer_token),
    auth_service: AuthService = Depends(get_auth_service),
) -> Any:
    user = auth_service.me(access_token=access_token)
    return success_response(user.model_dump(by_alias=True, mode="json"), request)


@auth_router.post("/refresh")
async def refresh(
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
) -> Any:
    refresh_token = request.cookies.get(auth_service.refresh_cookie_name)
    refresh_data, new_refresh_token = auth_service.refresh(refresh_token=refresh_token)
    http_response = success_response(refresh_data.model_dump(by_alias=True, mode="json"), request)
    if new_refresh_token:
        _set_refresh_cookie(http_response, auth_service=auth_service, refresh_token=new_refresh_token)
    return http_response


@auth_router.post("/logout")
async def logout(
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
) -> Any:
    refresh_token = request.cookies.get(auth_service.refresh_cookie_name)
    logout_data = auth_service.logout(refresh_token=refresh_token)
    http_response = success_response(logout_data.model_dump(by_alias=True, mode="json"), request)
    _clear_refresh_cookie(http_response, auth_service=auth_service)
    return http_response
