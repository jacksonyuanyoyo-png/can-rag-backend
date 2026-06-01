from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import FileResponse

from app.api.common import get_request_id, success_response
from app.core.config import get_settings
from app.api.schemas.upload import CompleteUploadRequest, PresignUploadRequest
from app.core.dependencies import extract_bearer_token, get_auth_service, require_app_state_service
from app.core.errors import BusinessError, ErrorCode
from app.domain.upload import PresignFileInput
from app.schemas.auth import UserMePublic
from app.services.auth.auth_service import AuthService
from app.services.upload_service import UploadService

upload_router = APIRouter(prefix="/v1/uploads", tags=["Uploads"])
dev_upload_router = APIRouter(prefix="/v1/_dev/uploads", tags=["Dev Uploads"])


def get_upload_service(request: Request) -> UploadService:
    return require_app_state_service(request, "upload_service", "UploadService")


UploadServiceDep = Annotated[UploadService, Depends(get_upload_service)]


def _require_file_upload(user: UserMePublic) -> None:
    if "kb:file:upload" not in user.permissions:
        raise BusinessError(ErrorCode.AUTH_FORBIDDEN)


def _current_user(
    access_token: str = Depends(extract_bearer_token),
    auth_service: AuthService = Depends(get_auth_service),
) -> UserMePublic:
    return auth_service.me(access_token=access_token)


CurrentUserDep = Annotated[UserMePublic, Depends(_current_user)]


@dev_upload_router.put("/{upload_id}")
async def dev_put_upload_object(
    upload_id: str,
    request: Request,
) -> Response:
    """Local dev: accept presigned PUT bytes into LOCAL_UPLOAD_ROOT (see presign uploadUrl)."""
    storage_key = request.headers.get("X-Storage-Key") or request.headers.get("x-storage-key")
    if not storage_key or not storage_key.strip():
        raise BusinessError(
            ErrorCode.VALIDATION_ERROR,
            message="X-Storage-Key header is required",
            details={"uploadId": upload_id},
        )

    relative = Path(storage_key.strip())
    if relative.is_absolute() or ".." in relative.parts:
        raise BusinessError(
            ErrorCode.VALIDATION_ERROR,
            message="Invalid storage key",
            details={"storageKey": storage_key},
        )

    body = await request.body()
    destination = get_settings().upload_root_resolved / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(body)

    digest = hashlib.md5(body).hexdigest()  # noqa: S324 — dev ETag only
    etag = f'"{digest}"'
    return Response(status_code=status.HTTP_200_OK, headers={"ETag": etag})


@upload_router.post("/presign", status_code=status.HTTP_201_CREATED)
async def presign_uploads(
    request: Request,
    body: PresignUploadRequest,
    service: UploadServiceDep,
    user: CurrentUserDep,
) -> dict[str, Any]:
    _require_file_upload(user)
    file_inputs = [
        PresignFileInput(
            file_name=item.file_name,
            mime_type=item.mime_type,
            size_bytes=item.size_bytes,
        )
        for item in body.files
    ]
    results = service.presign(
        knowledge_base_id=body.knowledge_base_id,
        files=file_inputs,
        user_id=user.id,
    )
    return success_response(
        data={"uploads": [result.to_api_dict() for result in results]},
        request_id=get_request_id(request),
    )


@upload_router.get("/assets/{storage_path:path}")
async def get_upload_asset(storage_path: str) -> FileResponse:
    """按 storage_key 读取落盘文件（如 kb_images/*.png），供原文对照 Markdown 图片展示。"""
    relative = Path(storage_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise BusinessError(
            ErrorCode.VALIDATION_ERROR,
            message="Invalid storage path",
            details={"storagePath": storage_path},
        )
    file_path = get_settings().upload_root_resolved / relative
    if not file_path.is_file():
        raise BusinessError(
            ErrorCode.RESOURCE_NOT_FOUND,
            details={"storagePath": storage_path},
        )
    return FileResponse(file_path)


@upload_router.post("/{upload_id}:complete")
async def complete_upload(
    request: Request,
    upload_id: str,
    body: CompleteUploadRequest,
    service: UploadServiceDep,
    user: CurrentUserDep,
) -> dict[str, Any]:
    _require_file_upload(user)
    result = service.complete(
        upload_id=upload_id,
        file_id=body.file_id,
        storage_key=body.storage_key,
        user_id=user.id,
        etag=body.etag,
    )
    return success_response(
        data=result.to_api_dict(),
        request_id=get_request_id(request),
    )
