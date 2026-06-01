from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from app.api.common import get_request_id, success_response
from app.core.dependencies import require_app_state_service
from app.schemas.model import ModelItem
from app.services.model_service import ModelService

model_router = APIRouter(prefix="/v1", tags=["Models"])


def get_model_service(request: Request) -> ModelService:
    return require_app_state_service(request, "model_service", "ModelService")


ModelServiceDep = Annotated[ModelService, Depends(get_model_service)]


def _model_items_to_api(models: list[ModelItem]) -> list[dict[str, Any]]:
    return [
        model.model_dump(by_alias=True, exclude_none=True)
        for model in models
    ]


@model_router.get("/models")
async def list_models(
    request: Request,
    service: ModelServiceDep,
) -> dict[str, Any]:
    """推理（对话）与 embedding 模型统一列表；用 tag 区分：inference / embedding。"""
    models = service.list_models()
    return success_response(
        data=_model_items_to_api(models),
        request_id=get_request_id(request),
    )


@model_router.get("/embedding-models")
async def list_embedding_models(
    request: Request,
    service: ModelServiceDep,
) -> dict[str, Any]:
    """仅 embedding 模型（兼容旧前端）；每项 tag 均为 embedding。"""
    models = [item.to_model_item() for item in service.list_embedding_models()]
    return success_response(
        data=_model_items_to_api(models),
        request_id=get_request_id(request),
    )
