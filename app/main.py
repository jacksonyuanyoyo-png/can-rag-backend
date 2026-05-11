from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.diagnostic_routes import diagnostic_router
from app.core.config import get_settings
from app.services.knowledge_base_service import KnowledgeBaseService
from app.repositories.knowledge_base_repository import KnowledgeBaseRepository
from app.services.rag.pipeline import RagPipeline


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    repository = KnowledgeBaseRepository(settings.metadata_path_resolved)
    rag_pipeline = RagPipeline(settings=settings)
    kb = KnowledgeBaseService(settings=settings, repository=repository, rag_pipeline=rag_pipeline)
    app.state.settings = settings
    app.state.knowledge_base_service = kb
    yield


app = FastAPI(title="Fidelity RAG Gateway", lifespan=lifespan)
app.include_router(diagnostic_router)
