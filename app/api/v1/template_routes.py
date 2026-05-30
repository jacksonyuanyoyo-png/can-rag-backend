from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status

from app.api.common import get_request_id, success_response
from app.api.schemas.template import (
    CreateTemplateRequest,
    DeleteTemplateResponse,
    UpdateTemplateRequest,
)
from app.core.dependencies import extract_bearer_token, get_auth_service, require_app_state_service
from app.core.errors import BusinessError, ErrorCode
from app.domain.template import Template
from app.schemas.auth import UserMePublic
from app.services.auth.auth_service import AuthService
from app.services.template_service import TemplateService

template_router = APIRouter(prefix="/v1/templates", tags=["Templates"])


def get_template_service(request: Request) -> TemplateService:
    return require_app_state_service(request, "template_service", "TemplateService")


TemplateServiceDep = Annotated[TemplateService, Depends(get_template_service)]


def _require_template_read(user: UserMePublic) -> None:
    if "template:read" not in user.permissions:
        raise BusinessError(ErrorCode.AUTH_FORBIDDEN)


def _require_template_write(user: UserMePublic) -> None:
    if "template:write" not in user.permissions:
        raise BusinessError(ErrorCode.AUTH_FORBIDDEN)


def _current_user(
    access_token: str = Depends(extract_bearer_token),
    auth_service: AuthService = Depends(get_auth_service),
) -> UserMePublic:
    return auth_service.me(access_token=access_token)


CurrentUserDep = Annotated[UserMePublic, Depends(_current_user)]


def _template_item(template: Template) -> dict[str, Any]:
    return template.to_api()


@template_router.get("")
async def list_templates(
    request: Request,
    service: TemplateServiceDep,
    user: CurrentUserDep,
) -> dict[str, Any]:
    _require_template_read(user)
    templates = service.list_templates(owner_user_id=user.id)
    return success_response(
        data=[_template_item(template) for template in templates],
        request_id=get_request_id(request),
    )


@template_router.post("", status_code=status.HTTP_201_CREATED)
async def create_template(
    request: Request,
    body: CreateTemplateRequest,
    service: TemplateServiceDep,
    user: CurrentUserDep,
) -> dict[str, Any]:
    _require_template_write(user)
    template = service.create_template(
        owner_user_id=user.id,
        name=body.name,
        content=body.content,
        snippet=body.snippet,
    )
    return success_response(
        data=_template_item(template),
        request_id=get_request_id(request),
    )


@template_router.patch("/{template_id}")
async def update_template(
    request: Request,
    template_id: str,
    body: UpdateTemplateRequest,
    service: TemplateServiceDep,
    user: CurrentUserDep,
) -> dict[str, Any]:
    _require_template_write(user)
    template = service.update_template(
        template_id,
        name=body.name,
        content=body.content,
        snippet=body.snippet,
    )
    return success_response(
        data=_template_item(template),
        request_id=get_request_id(request),
    )


@template_router.delete("/{template_id}")
async def delete_template(
    request: Request,
    template_id: str,
    service: TemplateServiceDep,
    user: CurrentUserDep,
) -> dict[str, Any]:
    _require_template_write(user)
    service.delete_template(template_id)
    return success_response(
        data=DeleteTemplateResponse().model_dump(),
        request_id=get_request_id(request),
    )
