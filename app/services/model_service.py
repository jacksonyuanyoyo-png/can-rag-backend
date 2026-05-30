from __future__ import annotations

import json
from typing import Any

from app.core.config import Settings
from app.domain.model import Model
from app.domain.model_catalog import (
    EmbeddingModelCatalogEntry,
    ModelCatalogEntry,
    openai_chat_model_catalog,
    openai_embedding_model_catalog,
)
from app.repositories.model_repository import ModelRepository
from app.schemas.model import EmbeddingModelItem, ModelItem

_FIELD_DEFAULTS = {"status": "active", "visibility": "system", "provider": "openai"}


class ModelService:
    """模型列表服务：优先 PostgreSQL（app.t_dim_model），未配置库时回退 JSON/默认目录。"""

    def __init__(
        self,
        settings: Settings,
        repository: ModelRepository | None = None,
    ) -> None:
        self._settings = settings
        self._repository = repository

    @staticmethod
    def catalog_entries() -> list[ModelCatalogEntry]:
        return openai_chat_model_catalog()

    @staticmethod
    def embedding_catalog_entries() -> list[EmbeddingModelCatalogEntry]:
        return openai_embedding_model_catalog()

    def list_embedding_models(self) -> list[EmbeddingModelItem]:
        default_id = self._settings.OPENAI_EMBEDDING_MODEL.strip()
        items: list[EmbeddingModelItem] = []
        for entry in self.embedding_catalog_entries():
            item = EmbeddingModelItem.model_validate(entry.to_api_dict())
            items.append(item)
        if default_id and all(item.id != default_id for item in items):
            items.insert(
                0,
                EmbeddingModelItem(
                    id=default_id,
                    name=default_id,
                    icon="/models/openai.svg",
                    provider="openai",
                    status="active",
                    dimensions=self._settings.RAG_EMBEDDING_DIMENSIONS,
                    maxInputTokens=8191,
                    description="来自 OPENAI_EMBEDDING_MODEL 环境变量",
                ),
            )
        return items

    def sync_catalog_to_database(self) -> None:
        if self._repository is None:
            return
        self._repository.sync_openai_catalog(self.catalog_entries())

    def list_models(self) -> list[ModelItem]:
        if self._repository is not None:
            db_models = self._repository.list_active()
            if db_models:
                return [self._to_model_item(model) for model in db_models]
        return self._load_models_from_config()

    @staticmethod
    def _to_model_item(model: Model) -> ModelItem:
        return ModelItem(
            id=model.id,
            name=model.display_name,
            icon=model.icon or "/models/openai.svg",
            provider=model.provider,
            status=model.status,
            visibility=model.visibility,
        )

    def _load_models_from_config(self) -> list[ModelItem]:
        raw: list[Any] | None = None

        if self._settings.MODELS_JSON.strip():
            try:
                parsed = json.loads(self._settings.MODELS_JSON)
                if isinstance(parsed, list):
                    raw = parsed
            except json.JSONDecodeError:
                pass

        if raw is None:
            models_path = self._settings.models_path_resolved
            if models_path.is_file():
                try:
                    parsed = json.loads(models_path.read_text(encoding="utf-8"))
                    if isinstance(parsed, list):
                        raw = parsed
                except (json.JSONDecodeError, OSError):
                    pass

        if raw is None:
            raw = [entry.to_api_dict() for entry in self.catalog_entries()]

        return [self._normalize_model(item) for item in raw if isinstance(item, dict)]

    @staticmethod
    def _normalize_model(entry: dict[str, Any]) -> ModelItem:
        merged = {**_FIELD_DEFAULTS, **entry}
        if "name" not in merged and "display_name" in merged:
            merged["name"] = merged["display_name"]
        return ModelItem.model_validate(merged)
