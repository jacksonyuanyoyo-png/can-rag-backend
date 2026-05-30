from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status

from app.api.common import get_request_id, success_response
from app.api.schemas.upload import CompleteUploadRequest, PresignUploadRequest
from app.core.dependencies import extract_bearer_token, get_auth_service, require_app_state_service
from app.core.errors import BusinessError, ErrorCode
from app.domain.upload import PresignFileInput
from app.schemas.auth import UserMePublic
from app.services.auth.auth_service import AuthService
from app.services.upload_service import UploadService

upload_router = APIRouter(prefix="/v1/uploads", tags=["Uploads"])


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
