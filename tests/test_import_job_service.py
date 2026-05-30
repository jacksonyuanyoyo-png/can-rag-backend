from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.api.schemas.import_job import ChunkingOptions, CreateImportJobRequest
from app.core.errors import BusinessError, ErrorCode
from app.domain.import_job import ChunkingConfig, ImportJobStage, ImportJobStatus
from app.repositories.idempotency_repository import IdempotencyRepository
from app.repositories.import_job_repository import ImportJobRepository
from app.services.import_job_service import (
    ImportJobCreateRequest,
    ImportJobRetryRequest,
    ImportJobService,
)


@pytest.fixture
def import_job_repo(database_url: str, db_connection) -> ImportJobRepository:
    repo = ImportJobRepository(database_url, connection=db_connection)
    repo.ensure_schema()
    return repo


@pytest.fixture
def idempotency_repo(database_url: str, db_connection) -> IdempotencyRepository:
    repo = IdempotencyRepository(database_url, connection=db_connection)
    repo.ensure_schema()
    return repo


@pytest.fixture
def import_job_service(
    import_job_repo: ImportJobRepository,
    idempotency_repo: IdempotencyRepository,
) -> ImportJobService:
    return ImportJobService(
        import_job_repository=import_job_repo,
        idempotency_repository=idempotency_repo,
        max_active_imports_per_kb=2,
    )


def _seed_kb(repo: ImportJobRepository, kb_id: str) -> None:
    repo.ensure_knowledge_base_stub(kb_id)


def test_create_and_get_import_job(
    import_job_service: ImportJobService,
    import_job_repo: ImportJobRepository,
) -> None:
    kb_id = f"kb_service_{uuid4().hex[:8]}"
    _seed_kb(import_job_repo, kb_id)
    result = import_job_service.create(
        ImportJobCreateRequest(
            kb_id=kb_id,
            file_ids=["file_a", "file_b"],
            chunk_strategy="default",
            meta_filename=True,
            meta_headings=False,
        ),
        user_id="user_1",
    )

    assert result.replayed is False
    assert result.job.status == ImportJobStatus.QUEUED
    assert result.job.stage == ImportJobStage.UPLOAD
    assert result.job.file_ids == ["file_a", "file_b"]
    assert result.job.option is not None
    assert result.job.option.chunk_strategy == "semantic"

    loaded = import_job_service.get(result.job.id)
    assert loaded.id == result.job.id


def test_create_idempotency_replay(
    import_job_service: ImportJobService,
    import_job_repo: ImportJobRepository,
) -> None:
    kb_id = f"kb_idem_{uuid4().hex[:8]}"
    _seed_kb(import_job_repo, kb_id)
    request = ImportJobCreateRequest(
        kb_id=kb_id,
        file_ids=["file_1"],
        chunk_strategy="page",
    )
    key = f"idem-{uuid4().hex}"

    first = import_job_service.create(request, user_id="user_1", idempotency_key=key)
    replay = import_job_service.create(request, user_id="user_1", idempotency_key=key)

    assert replay.replayed is True
    assert replay.job.id == first.job.id


def test_create_idempotency_conflict(
    import_job_service: ImportJobService,
    import_job_repo: ImportJobRepository,
) -> None:
    kb_id = f"kb_conflict_{uuid4().hex[:8]}"
    _seed_kb(import_job_repo, kb_id)
    key = f"idem-{uuid4().hex}"

    import_job_service.create(
        ImportJobCreateRequest(kb_id=kb_id, file_ids=["file_1"], chunk_strategy="default"),
        user_id="user_1",
        idempotency_key=key,
    )

    with pytest.raises(BusinessError) as exc_info:
        import_job_service.create(
            ImportJobCreateRequest(kb_id=kb_id, file_ids=["file_2"], chunk_strategy="default"),
            user_id="user_1",
            idempotency_key=key,
        )

    assert exc_info.value.code == ErrorCode.IDEMPOTENCY_CONFLICT


def test_cancel_import_job(
    import_job_service: ImportJobService,
    import_job_repo: ImportJobRepository,
) -> None:
    kb_id = f"kb_cancel_{uuid4().hex[:8]}"
    _seed_kb(import_job_repo, kb_id)
    created = import_job_service.create(
        ImportJobCreateRequest(kb_id=kb_id, file_ids=["file_1"], chunk_strategy="default"),
        user_id="user_1",
    )

    cancelled = import_job_service.cancel(created.job.id)
    assert cancelled.status == ImportJobStatus.CANCELLED


def test_cancel_completed_job_rejected(import_job_service: ImportJobService, import_job_repo) -> None:
    kb_id = f"kb_done_{uuid4().hex[:8]}"
    _seed_kb(import_job_repo, kb_id)
    created = import_job_service.create(
        ImportJobCreateRequest(
            kb_id=kb_id,
            file_ids=["file_1"],
            chunk_strategy="default",
        ),
        user_id="user_1",
    )
    import_job_repo.update_progress(
        created.job.id,
        status=ImportJobStatus.RUNNING,
    )
    import_job_repo.update_progress(
        created.job.id,
        status=ImportJobStatus.COMPLETED,
        stage=ImportJobStage.DONE,
        progress=100,
    )

    with pytest.raises(BusinessError) as exc_info:
        import_job_service.cancel(created.job.id)

    assert exc_info.value.code == ErrorCode.KB_STATUS_CONFLICT


def test_retry_failed_import_job(import_job_service: ImportJobService, import_job_repo) -> None:
    kb_id = f"kb_retry_{uuid4().hex[:8]}"
    _seed_kb(import_job_repo, kb_id)
    created = import_job_service.create(
        ImportJobCreateRequest(kb_id=kb_id, file_ids=["file_1"], chunk_strategy="default"),
        user_id="user_1",
    )
    import_job_repo.update_progress(created.job.id, status=ImportJobStatus.RUNNING)
    import_job_repo.update_progress(
        created.job.id,
        status=ImportJobStatus.FAILED,
        stage=ImportJobStage.PARSE,
        error_code="IMPORT_PARSE_FAILED",
        error_message="parse failed",
    )

    retried = import_job_service.retry(
        ImportJobRetryRequest(job_id=created.job.id, chunk_strategy="custom"),
        user_id="user_1",
    )

    assert retried.replayed is False
    assert retried.job.retry_of == created.job.id
    assert retried.job.status == ImportJobStatus.QUEUED
    assert retried.job.option is not None
    assert retried.job.option.chunk_strategy == "fixed_size"


def test_create_concurrency_limit(
    import_job_service: ImportJobService,
    import_job_repo: ImportJobRepository,
) -> None:
    kb_id = f"kb_limit_{uuid4().hex[:8]}"
    _seed_kb(import_job_repo, kb_id)
    import_job_service.create(
        ImportJobCreateRequest(kb_id=kb_id, file_ids=["file_1"], chunk_strategy="default"),
        user_id="user_1",
    )
    import_job_service.create(
        ImportJobCreateRequest(kb_id=kb_id, file_ids=["file_2"], chunk_strategy="default"),
        user_id="user_1",
    )

    with pytest.raises(BusinessError) as exc_info:
        import_job_service.create(
            ImportJobCreateRequest(kb_id=kb_id, file_ids=["file_3"], chunk_strategy="default"),
            user_id="user_1",
        )

    assert exc_info.value.code == ErrorCode.IMPORT_CONCURRENCY_LIMIT


def test_chunking_config_default_from_none_options() -> None:
    config = ChunkingConfig.from_chunking_options(
        None,
        fallback_strategy="default",
        metadata=None,
    )

    assert config.strategy == "default"
    assert config.mode is None
    assert config.index_size == 512
    assert config.meta_filename is True
    assert config.meta_headings is False


def test_chunking_config_from_default_chunking_options() -> None:
    req = CreateImportJobRequest.model_validate(
        {"fileIds": ["f1"], "chunking": {"strategy": "default"}}
    )
    config = ChunkingConfig.from_chunking_options(
        req.chunking,
        fallback_strategy=req.chunk_strategy,
        metadata=req.metadata,
    )

    assert config.strategy == "default"
    assert config.mode is None
    assert config.index_size == 512


def test_chunking_config_custom_paragraph_mode() -> None:
    req = CreateImportJobRequest.model_validate(
        {
            "fileIds": ["f1"],
            "chunking": {
                "strategy": "custom",
                "custom": {"mode": "paragraph"},
                "paragraph": {"useModel": True, "maxDepth": 4},
                "indexSize": 256,
            },
        }
    )
    config = ChunkingConfig.from_chunking_options(
        req.chunking,
        fallback_strategy=req.chunk_strategy,
        metadata=req.metadata,
    )

    assert config.strategy == "custom"
    assert config.mode == "paragraph"
    assert config.paragraph_use_model is True
    assert config.paragraph_max_depth == 4
    assert config.index_size == 256


def test_chunking_config_custom_length_mode() -> None:
    req = CreateImportJobRequest.model_validate(
        {
            "fileIds": ["f1"],
            "chunking": {
                "strategy": "custom",
                "custom": {"mode": "length"},
                "length": {
                    "chunkSize": 400,
                    "overlap": 40,
                    "maxChunkSize": 800,
                },
            },
        }
    )
    config = ChunkingConfig.from_chunking_options(
        req.chunking,
        fallback_strategy=req.chunk_strategy,
        metadata=req.metadata,
    )

    assert config.mode == "length"
    assert config.chunk_size == 400
    assert config.overlap == 40
    assert config.max_chunk_size == 800


def test_chunking_config_custom_separator_mode() -> None:
    req = CreateImportJobRequest.model_validate(
        {
            "fileIds": ["f1"],
            "chunking": {
                "strategy": "custom",
                "custom": {"mode": "separator"},
                "separator": {"separators": ["\n\n", "---"]},
            },
        }
    )
    config = ChunkingConfig.from_chunking_options(
        req.chunking,
        fallback_strategy=req.chunk_strategy,
        metadata=req.metadata,
    )

    assert config.mode == "separator"
    assert config.separators == ["\n\n", "---"]


def test_chunking_config_to_dict_from_dict_roundtrip() -> None:
    original = ChunkingConfig(
        strategy="custom",
        mode="length",
        chunk_size=512,
        overlap=50,
        max_chunk_size=1024,
        index_size=1024,
        meta_filename=False,
        meta_headings=True,
    )

    restored = ChunkingConfig.from_dict(original.to_dict())

    assert restored == original


def test_create_rejects_whole_chunking_strategy(
    import_job_service: ImportJobService,
    import_job_repo: ImportJobRepository,
) -> None:
    kb_id = f"kb_whole_{uuid4().hex[:8]}"
    _seed_kb(import_job_repo, kb_id)

    with pytest.raises(BusinessError) as exc_info:
        import_job_service.create(
            ImportJobCreateRequest(
                kb_id=kb_id,
                file_ids=["file_1"],
                chunk_strategy="whole",
                chunking=ChunkingConfig(strategy="whole"),
            ),
            user_id="user_1",
        )

    assert exc_info.value.code == ErrorCode.IMPORT_INVALID_OPTIONS


def test_create_passes_chunking_config_to_repository(
    import_job_repo: ImportJobRepository,
    idempotency_repo: IdempotencyRepository,
) -> None:
    kb_id = f"kb_chunk_cfg_{uuid4().hex[:8]}"
    _seed_kb(import_job_repo, kb_id)
    mock_repo = MagicMock(wraps=import_job_repo)
    service = ImportJobService(
        import_job_repository=mock_repo,
        idempotency_repository=idempotency_repo,
        max_active_imports_per_kb=2,
    )
    chunking = ChunkingConfig(
        strategy="custom",
        mode="length",
        chunk_size=300,
        overlap=30,
        max_chunk_size=600,
        index_size=512,
    )

    service.create(
        ImportJobCreateRequest(
            kb_id=kb_id,
            file_ids=["file_1"],
            chunk_strategy=chunking.strategy,
            meta_filename=chunking.meta_filename,
            meta_headings=chunking.meta_headings,
            chunking=chunking,
        ),
        user_id="user_1",
    )

    mock_repo.create.assert_called_once()
    _, kwargs = mock_repo.create.call_args
    assert kwargs["chunk_strategy"] == "custom"
    assert kwargs["chunking_config"] == chunking.to_dict()


def test_create_with_chunking_preserves_frontend_strategy(
    import_job_service: ImportJobService,
    import_job_repo: ImportJobRepository,
) -> None:
    kb_id = f"kb_chunk_strat_{uuid4().hex[:8]}"
    _seed_kb(import_job_repo, kb_id)
    chunking = ChunkingConfig.from_chunking_options(
        ChunkingOptions.model_validate({"strategy": "default"}),
        fallback_strategy="default",
        metadata=None,
    )

    result = import_job_service.create(
        ImportJobCreateRequest(
            kb_id=kb_id,
            file_ids=["file_1"],
            chunk_strategy=chunking.strategy,
            meta_filename=chunking.meta_filename,
            meta_headings=chunking.meta_headings,
            chunking=chunking,
        ),
        user_id="user_1",
    )

    assert result.job.option is not None
    assert result.job.option.chunk_strategy == "default"
