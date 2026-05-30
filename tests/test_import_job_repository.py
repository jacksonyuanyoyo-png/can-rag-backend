from __future__ import annotations

from uuid import uuid4

import pytest

from app.domain.import_job import ImportJobStage, ImportJobStatus, ImportJobTransitionError
from app.repositories.import_job_repository import ImportJobRepository


@pytest.fixture
def import_job_repo(database_url: str, db_connection) -> ImportJobRepository:
    repo = ImportJobRepository(database_url, connection=db_connection)
    repo.ensure_schema()
    return repo


def _seed_kb(repo: ImportJobRepository, kb_id: str) -> None:
    repo.ensure_knowledge_base_stub(kb_id)


def test_create_and_get_import_job(import_job_repo: ImportJobRepository) -> None:
    _seed_kb(import_job_repo, "kb_test")
    job = import_job_repo.create(
        kb_id="kb_test",
        file_ids=["file_a", "file_b"],
        chunk_strategy="fixed_size",
        meta_filename=True,
        meta_headings=False,
    )

    assert job.status == ImportJobStatus.QUEUED
    assert job.progress == 0
    assert job.stage == ImportJobStage.UPLOAD
    assert job.file_ids == ["file_a", "file_b"]
    assert job.option is not None
    assert job.option.chunk_strategy == "fixed_size"

    loaded = import_job_repo.get(job.id)
    assert loaded is not None
    assert loaded.id == job.id
    assert loaded.file_ids == job.file_ids


def test_update_progress_and_cancel(import_job_repo: ImportJobRepository) -> None:
    _seed_kb(import_job_repo, "kb_progress")
    job = import_job_repo.create(
        kb_id="kb_progress",
        file_ids=["file_1"],
        chunk_strategy="default",
    )

    updated = import_job_repo.update_progress(
        job.id,
        status=ImportJobStatus.RUNNING,
        progress=45,
        stage=ImportJobStage.EMBED,
    )
    assert updated is not None
    assert updated.status == ImportJobStatus.RUNNING
    assert updated.progress == 45
    assert updated.stage == ImportJobStage.EMBED

    cancelled = import_job_repo.cancel(job.id)
    assert cancelled is not None
    assert cancelled.status == ImportJobStatus.CANCELLED


def test_count_active_by_kb(import_job_repo: ImportJobRepository) -> None:
    kb_id = f"kb_active_{uuid4().hex[:8]}"
    _seed_kb(import_job_repo, kb_id)
    import_job_repo.create(kb_id=kb_id, file_ids=["file_1"], chunk_strategy="default")
    running_job = import_job_repo.create(
        kb_id=kb_id,
        file_ids=["file_2"],
        chunk_strategy="default",
    )
    import_job_repo.update_progress(running_job.id, status=ImportJobStatus.RUNNING)

    assert import_job_repo.count_active_by_kb(kb_id) == 2

    import_job_repo.cancel(running_job.id)
    assert import_job_repo.count_active_by_kb(kb_id) == 1


def test_create_requires_file_ids(import_job_repo: ImportJobRepository) -> None:
    with pytest.raises(ValueError, match="至少需要一个 file_id"):
        import_job_repo.create(
            kb_id="kb_empty",
            file_ids=[],
            chunk_strategy="default",
        )


def test_invalid_status_transition(import_job_repo: ImportJobRepository) -> None:
    _seed_kb(import_job_repo, "kb_transition")
    job = import_job_repo.create(
        kb_id="kb_transition",
        file_ids=["file_1"],
        chunk_strategy="default",
    )
    import_job_repo.update_progress(job.id, status=ImportJobStatus.RUNNING)
    import_job_repo.update_progress(job.id, status=ImportJobStatus.COMPLETED, stage=ImportJobStage.DONE)

    with pytest.raises(ImportJobTransitionError):
        import_job_repo.update_progress(job.id, status=ImportJobStatus.RUNNING)


def test_create_chunking_config_round_trip(import_job_repo: ImportJobRepository) -> None:
    kb_id = f"kb_chunking_{uuid4().hex[:8]}"
    _seed_kb(import_job_repo, kb_id)
    nested_config = {
        "mode": "semantic",
        "params": {"max_tokens": 512, "overlap": 64},
        "nested": {"enabled": True, "tags": ["a", "b"]},
    }
    job = import_job_repo.create(
        kb_id=kb_id,
        file_ids=["file_chunk"],
        chunk_strategy="fixed_size",
        chunking_config=nested_config,
    )
    assert import_job_repo.get_chunking_config(job.id) == nested_config

    job_default = import_job_repo.create(
        kb_id=kb_id,
        file_ids=["file_no_chunking"],
        chunk_strategy="default",
    )
    assert import_job_repo.get_chunking_config(job_default.id) is None


def test_invalid_stage_regression(import_job_repo: ImportJobRepository) -> None:
    _seed_kb(import_job_repo, "kb_stage")
    job = import_job_repo.create(
        kb_id="kb_stage",
        file_ids=["file_1"],
        chunk_strategy="default",
    )
    import_job_repo.update_progress(job.id, stage=ImportJobStage.EMBED)

    with pytest.raises(ValueError, match="stage 不允许"):
        import_job_repo.update_progress(job.id, stage=ImportJobStage.PARSE)
