from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from app.core.config import Settings
from app.core.database import initialize_database, is_database_configured
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.folder_repository import FolderRepository
from app.repositories.idempotency_repository import IdempotencyRepository
from app.repositories.import_job_repository import ImportJobRepository
from app.repositories.knowledge_base_repository import KnowledgeBaseRepository
from app.repositories.model_repository import ModelRepository
from app.repositories.template_repository import TemplateRepository
from app.repositories.upload_repository import UploadRepository
from app.services.auth.auth_service import AuthService
from app.services.auth.refresh_store import InMemoryRefreshStore
from app.services.conversation_service import ConversationService
from app.services.folder_service import FolderService
from app.services.import_job_runner import (
    ImportJobPoller,
    KbCountSink,
    KbFileResolver,
    KbFileStatusSink,
    build_process_file,
)
from app.services.import_job_service import ImportJobService
from app.services.import_job_worker import ImportJobWorker
from app.services.knowledge_base_service import KnowledgeBaseService
from app.services.model_service import ModelService
from app.services.rag.pipeline import RagPipeline
from app.services.template_service import TemplateService
from app.services.upload_service import UploadService
from app.services.web_import_service import WebImportService

_DB_DEPENDENT_STATE_KEYS = (
    "template_service",
    "folder_service",
    "upload_service",
    "web_import_service",
    "idempotency_repository",
    "import_job_service",
)

_PG_RAG_BACKENDS = frozenset({"postgres_pgvector", "langchain_pgvector"})


def _settings_for_runtime(settings: Settings) -> Settings:
    """无 DATABASE_URL 时降级 PG 依赖项，避免 lifespan 启动崩溃。"""
    if is_database_configured(settings):
        return settings
    updates: dict[str, str] = {}
    if settings.RAG_BACKEND in _PG_RAG_BACKENDS:
        updates["RAG_BACKEND"] = "local"
    return settings.model_copy(update=updates) if updates else settings


def wire_app_state(app: FastAPI, settings: Settings) -> dict[str, Any]:
    """装配 lifespan 所需的全部 app.state 服务。

    无 DATABASE_URL 时跳过 PostgreSQL 仓储，对应 state 置为 None，避免启动崩溃。
    """
    runtime_settings = _settings_for_runtime(settings)
    app.state.settings = runtime_settings
    app.state.database_status = initialize_database(settings)

    kb_repository = KnowledgeBaseRepository(runtime_settings.metadata_path_resolved)
    rag_pipeline = RagPipeline(settings=runtime_settings)
    knowledge_base_service = KnowledgeBaseService(
        settings=runtime_settings,
        repository=kb_repository,
        rag_pipeline=rag_pipeline,
    )
    app.state.knowledge_base_service = knowledge_base_service
    app.state.conversation_service = ConversationService(
        ConversationRepository(),
        settings=runtime_settings,
        knowledge_base_service=knowledge_base_service,
    )
    app.state.auth_service = AuthService(
        settings=runtime_settings,
        refresh_store=InMemoryRefreshStore(),
    )

    if is_database_configured(settings):
        _wire_database_services(app, runtime_settings, kb_repository, rag_pipeline)
    else:
        for key in _DB_DEPENDENT_STATE_KEYS:
            setattr(app.state, key, None)
        app.state.import_job_worker = None
        app.state.import_job_poller = None
        app.state.model_service = ModelService(settings=runtime_settings, repository=None)

    return app.state.database_status


def _wire_database_services(
    app: FastAPI,
    settings: Settings,
    kb_repository: KnowledgeBaseRepository,
    rag_pipeline: RagPipeline,
) -> None:
    database_url = settings.DATABASE_URL

    template_repository = TemplateRepository(database_url)
    template_repository.ensure_schema()
    app.state.template_service = TemplateService(template_repository)

    folder_repository = FolderRepository(database_url)
    folder_repository.ensure_schema()
    app.state.folder_service = FolderService(folder_repository)

    upload_repository = UploadRepository(database_url)
    upload_repository.ensure_schema()
    app.state.knowledge_base_service.attach_upload_repository(upload_repository)
    app.state.upload_service = UploadService(
        settings=settings,
        upload_repository=upload_repository,
        knowledge_base_repository=kb_repository,
        rag_pipeline=rag_pipeline,
    )

    idempotency_repository = IdempotencyRepository(database_url)
    idempotency_repository.ensure_schema()
    app.state.idempotency_repository = idempotency_repository

    import_job_repository = ImportJobRepository(database_url)
    import_job_repository.ensure_schema()
    import_job_service = ImportJobService(
        import_job_repository=import_job_repository,
        idempotency_repository=idempotency_repository,
    )
    app.state.import_job_service = import_job_service

    app.state.web_import_service = WebImportService(
        settings=settings,
        upload_repository=upload_repository,
        knowledge_base_repository=kb_repository,
        import_job_service=import_job_service,
    )

    resolver = KbFileResolver(settings)
    process_file = build_process_file(
        pipeline=rag_pipeline,
        resolver=resolver,
        jobs=import_job_repository,
        kb_repository=kb_repository,
        settings=settings,
    )
    worker = ImportJobWorker(
        jobs=import_job_repository,
        process_file=process_file,
        file_status_sink=KbFileStatusSink(settings),
        kb_count_sink=KbCountSink(settings),
    )
    app.state.import_job_worker = worker
    app.state.import_job_poller = ImportJobPoller(
        worker=worker,
        database_url=database_url,
    )

    model_repository = ModelRepository(database_url)
    model_repository.ensure_schema()
    model_service = ModelService(settings=settings, repository=model_repository)
    model_service.sync_catalog_to_database()
    app.state.model_service = model_service
