from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status

from app.api.common import get_request_id, success_response
from app.api.schemas.folder import CreateFolderRequest, DeleteFolderResponse, UpdateFolderRequest
from app.core.dependencies import extract_bearer_token, get_auth_service, require_app_state_service
from app.core.errors import BusinessError, ErrorCode
from app.domain.folder import Folder
from app.schemas.auth import UserMePublic
from app.services.auth.auth_service import AuthService
from app.services.folder_service import FolderService

folder_router = APIRouter(prefix="/v1/folders", tags=["Folders"])


def get_folder_service(request: Request) -> FolderService:
    return require_app_state_service(request, "folder_service", "FolderService")


FolderServiceDep = Annotated[FolderService, Depends(get_folder_service)]


def _require_folder_read(user: UserMePublic) -> None:
    if "folder:read" not in user.permissions:
        raise BusinessError(ErrorCode.AUTH_FORBIDDEN)


def _require_folder_write(user: UserMePublic) -> None:
    if "folder:write" not in user.permissions:
        raise BusinessError(ErrorCode.AUTH_FORBIDDEN)


def _resolve_team_id(request: Request) -> str | None:
    header = request.headers.get("X-Team-Id")
    if header and header.strip():
        return header.strip()
    return None


def _current_user(
    request: Request,
    access_token: str = Depends(extract_bearer_token),
    auth_service: AuthService = Depends(get_auth_service),
) -> UserMePublic:
    return auth_service.me(access_token=access_token)


CurrentUserDep = Annotated[UserMePublic, Depends(_current_user)]


def _folder_list_item(folder: Folder) -> dict[str, Any]:
    return {"id": folder.id, "name": folder.name}


def _folder_detail(folder: Folder) -> dict[str, Any]:
    return folder.to_api()


@folder_router.get("")
async def list_folders(
    request: Request,
    service: FolderServiceDep,
    user: CurrentUserDep,
) -> dict[str, Any]:
    _require_folder_read(user)
    folders = service.list_folders(
        owner_user_id=user.id,
        team_id=_resolve_team_id(request),
    )
    return success_response(
        data=[_folder_list_item(folder) for folder in folders],
        request_id=get_request_id(request),
    )


@folder_router.post("", status_code=status.HTTP_201_CREATED)
async def create_folder(
    request: Request,
    body: CreateFolderRequest,
    service: FolderServiceDep,
    user: CurrentUserDep,
) -> dict[str, Any]:
    _require_folder_write(user)
    folder = service.create_folder(
        name=body.name,
        owner_user_id=user.id,
        team_id=_resolve_team_id(request),
    )
    return success_response(
        data=_folder_list_item(folder),
        request_id=get_request_id(request),
    )


@folder_router.patch("/{folder_id}")
async def update_folder(
    request: Request,
    folder_id: str,
    body: UpdateFolderRequest,
    service: FolderServiceDep,
    user: CurrentUserDep,
) -> dict[str, Any]:
    _require_folder_write(user)
    folder = service.update_folder(
        folder_id=folder_id,
        name=body.name,
        owner_user_id=user.id,
    )
    return success_response(
        data=_folder_detail(folder),
        request_id=get_request_id(request),
    )


@folder_router.delete("/{folder_id}")
async def delete_folder(
    request: Request,
    folder_id: str,
    service: FolderServiceDep,
    user: CurrentUserDep,
) -> dict[str, Any]:
    _require_folder_write(user)
    service.delete_folder(folder_id=folder_id, owner_user_id=user.id)
    return success_response(
        data=DeleteFolderResponse().model_dump(),
        request_id=get_request_id(request),
    )
