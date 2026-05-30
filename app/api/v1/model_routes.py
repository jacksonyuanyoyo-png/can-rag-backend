from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from app.api.common import get_request_id, success_response
from app.core.dependencies import require_app_state_service
from app.services.model_service import ModelService

model_router = APIRouter(prefix="/v1", tags=["Models"])


def get_model_service(request: Request) -> ModelService:
    return require_app_state_service(request, "model_service", "ModelService")


ModelServiceDep = Annotated[ModelService, Depends(get_model_service)]


@model_router.get("/models")
async def list_models(
    request: Request,
    service: ModelServiceDep,
) -> dict[str, Any]:
    models = service.list_models()
    return success_response(
        data=[model.model_dump() for model in models],
        request_id=get_request_id(request),
    )


@model_router.get("/embedding-models")
async def list_embedding_models(
    request: Request,
    service: ModelServiceDep,
) -> dict[str, Any]:
    models = service.list_embedding_models()
    return success_response(
        data=[model.model_dump(by_alias=True) for model in models],
        request_id=get_request_id(request),
    )
