from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app.domain.import_job import (
    ChunkingConfig,
    ImportJob,
    ImportJobStage,
    ImportJobStatus,
)
from app.core.config import Settings
from app.domain.knowledge_base import KnowledgeBaseMetadata
from app.repositories.knowledge_base_repository import KnowledgeBaseRepository
from app.services.import_job_runner import ImportJobPoller, build_process_file
from app.services.rag.kb_embedding import KbEmbeddingConfig


@dataclass
class FakeJobsRepo:
    chunking_config: dict | None = field(default_factory=lambda: {"strategy": "default"})
    calls: list[str] = field(default_factory=list)

    def get_chunking_config(self, import_job_id: str) -> dict | None:
        self.calls.append(import_job_id)
        return self.chunking_config


@dataclass
class IndexDataCall:
    knowledge_base: str
    file_id: str
    file_name: str
    config: ChunkingConfig


class FakePipeline:
    def __init__(self) -> None:
        self.calls: list[IndexDataCall] = []

    def index_data(
        self,
        *,
        knowledge_base: str,
        file_id: str,
        document: object,
        config: ChunkingConfig,
        file_name: str,
        embedding_config: KbEmbeddingConfig | None = None,
        force_image_description: bool = False,
        on_progress: object = None,
    ) -> dict[str, int]:
        del document, force_image_description, on_progress
        self.calls.append(
            IndexDataCall(
                knowledge_base=knowledge_base,
                file_id=file_id,
                file_name=file_name,
                config=config,
            )
        )
        self.last_embedding_config = embedding_config
        return {"data": 3, "index": 5}


def _sample_job(*, kb_id: str = "kb_1", file_ids: list[str] | None = None) -> ImportJob:
    return ImportJob(
        id="job_1",
        kb_id=kb_id,
        file_ids=file_ids or ["file_1"],
        status=ImportJobStatus.QUEUED,
        progress=0,
        stage=ImportJobStage.UPLOAD,
    )


def test_build_process_file_success(tmp_path: Path) -> None:
    txt_path = tmp_path / "doc.txt"
    txt_path.write_text("hello world", encoding="utf-8")

    def fake_resolver(*, kb_id: str, file_id: str) -> tuple[str, Path]:
        assert kb_id == "kb_1"
        assert file_id == "file_1"
        return "doc.txt", txt_path

    pipeline = FakePipeline()
    jobs = FakeJobsRepo()
    metadata_path = tmp_path / "knowledge_bases.json"
    metadata_path.write_text('{"knowledge_bases": {}}', encoding="utf-8")
    kb_repo = KnowledgeBaseRepository(metadata_path)
    metadata = KnowledgeBaseMetadata(name="demo", description="")
    metadata.id = "kb_1"
    metadata.backend_refs["embedding_model_id"] = "text-embedding-3-small"
    kb_repo.save(metadata)
    settings = Settings(DATABASE_URL="", RAG_EMBEDDING_DIMENSIONS=1536)
    process_file = build_process_file(
        pipeline=pipeline,
        resolver=fake_resolver,
        jobs=jobs,
        kb_repository=kb_repo,
        settings=settings,
    )

    from app.services.import_job_progress import NullImportJobProgressReporter

    result = process_file(
        job=_sample_job(),
        file_id="file_1",
        file_index=0,
        progress=NullImportJobProgressReporter(),
    )

    assert result == 3
    assert len(pipeline.calls) == 1
    call = pipeline.calls[0]
    assert call.knowledge_base == "kb_1"
    assert call.file_id == "file_1"
    assert call.file_name == "doc.txt"
    assert call.config.strategy == "default"
    assert jobs.calls == ["job_1"]
    assert pipeline.last_embedding_config is not None
    assert pipeline.last_embedding_config.model_id == "text-embedding-3-small"


def test_build_process_file_unsupported_extension(tmp_path: Path) -> None:
    bad_path = tmp_path / "data.xyz"
    bad_path.write_text("x", encoding="utf-8")

    def fake_resolver(*, kb_id: str, file_id: str) -> tuple[str, Path]:
        del kb_id, file_id
        return "data.xyz", bad_path

    metadata_path = tmp_path / "knowledge_bases.json"
    metadata_path.write_text('{"knowledge_bases": {}}', encoding="utf-8")
    kb_repo = KnowledgeBaseRepository(metadata_path)
    settings = Settings(DATABASE_URL="")
    process_file = build_process_file(
        pipeline=FakePipeline(),
        resolver=fake_resolver,
        jobs=FakeJobsRepo(),
        kb_repository=kb_repo,
        settings=settings,
    )

    with pytest.raises(ValueError, match="不支持的文件类型"):
        from app.services.import_job_progress import NullImportJobProgressReporter

        process_file(
            job=_sample_job(),
            file_id="file_1",
            file_index=0,
            progress=NullImportJobProgressReporter(),
        )


@dataclass
class FakeWorker:
    run_calls: list[str] = field(default_factory=list)

    def run_job(self, job_id: str) -> ImportJob:
        self.run_calls.append(job_id)
        return _sample_job()


def test_poller_poll_once_processes_all_queued_jobs() -> None:
    worker = FakeWorker()
    poller = ImportJobPoller(
        worker=worker,  # type: ignore[arg-type]
        database_url="postgresql://unused",
        fetch_queued_job_ids_fn=lambda: ["job_a", "job_b"],
    )

    poller.poll_once()

    assert worker.run_calls == ["job_a", "job_b"]
