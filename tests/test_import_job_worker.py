from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from app.domain.import_job import (
    ALLOWED_STATUS_TRANSITIONS,
    ImportJob,
    ImportJobFileStatus,
    ImportJobOption,
    ImportJobStage,
    ImportJobStatus,
    validate_stage_transition,
    validate_status_transition,
)
from app.services.import_job_worker import ImportJobWorker


@dataclass
class ProgressUpdateCall:
    job_id: str
    status: ImportJobStatus | None
    progress: int | None
    stage: ImportJobStage | None
    error_code: str | None
    error_message: str | None
    clear_error: bool


@dataclass
class FakeImportJobRepository:
    _jobs: dict[str, ImportJob] = field(default_factory=dict)
    update_calls: list[ProgressUpdateCall] = field(default_factory=list)

    def seed(self, job: ImportJob) -> None:
        self._jobs[job.id] = job

    def get(self, job_id: str) -> ImportJob | None:
        return self._jobs.get(job_id)

    def update_progress(
        self,
        job_id: str,
        *,
        status: ImportJobStatus | None = None,
        progress: int | None = None,
        stage: ImportJobStage | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        clear_error: bool = False,
    ) -> ImportJob | None:
        current = self.get(job_id)
        if current is None:
            return None

        self.update_calls.append(
            ProgressUpdateCall(
                job_id=job_id,
                status=status,
                progress=progress,
                stage=stage,
                error_code=error_code,
                error_message=error_message,
                clear_error=clear_error,
            )
        )

        if status is not None:
            validate_status_transition(current.status, status)
        if stage is not None:
            validate_stage_transition(current.stage, stage)

        new_status = status if status is not None else current.status
        new_stage = stage if stage is not None else current.stage
        new_progress = progress if progress is not None else current.progress
        new_error_code = current.error_code
        new_error_message = current.error_message

        if clear_error:
            new_error_code = None
            new_error_message = None
        else:
            if error_code is not None:
                new_error_code = error_code
            if error_message is not None:
                new_error_message = error_message

        if progress is not None and (progress < 0 or progress > 100):
            raise ValueError("progress 必须在 0-100 之间。")
        if progress is not None and status not in (
            ImportJobStatus.FAILED,
            ImportJobStatus.CANCELLED,
        ):
            progress = max(current.progress, progress)

        updated = ImportJob(
            id=current.id,
            kb_id=current.kb_id,
            file_ids=list(current.file_ids),
            status=new_status,
            progress=new_progress,
            stage=new_stage,
            error_code=new_error_code,
            error_message=new_error_message,
            retry_of=current.retry_of,
            option=current.option,
            created_at=current.created_at,
            updated_at=datetime.now(UTC),
        )
        self._jobs[job_id] = updated
        return updated


@dataclass
class FileMark:
    kb_id: str
    file_id: str
    status: ImportJobFileStatus
    error: str | None = None


class RecordingFileStatusSink:
    def __init__(self) -> None:
        self.marks: list[FileMark] = []

    def mark_file(
        self,
        *,
        kb_id: str,
        file_id: str,
        status: ImportJobFileStatus,
        error: str | None = None,
    ) -> None:
        self.marks.append(
            FileMark(kb_id=kb_id, file_id=file_id, status=status, error=error)
        )


class RecordingKbCountSink:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def add_counts(
        self,
        *,
        kb_id: str,
        file_count_delta: int,
        chunk_count_delta: int,
    ) -> None:
        self.calls.append(
            {
                "kb_id": kb_id,
                "file_count_delta": file_count_delta,
                "chunk_count_delta": chunk_count_delta,
            }
        )


def _make_job(
    *,
    job_id: str = "job_test",
    kb_id: str = "kb_test",
    file_ids: list[str] | None = None,
    status: ImportJobStatus = ImportJobStatus.QUEUED,
    stage: ImportJobStage = ImportJobStage.UPLOAD,
    progress: int = 0,
) -> ImportJob:
    return ImportJob(
        id=job_id,
        kb_id=kb_id,
        file_ids=file_ids or ["file_a", "file_b"],
        status=status,
        progress=progress,
        stage=stage,
        option=ImportJobOption(chunk_strategy="default"),
    )


def _worker(
    repo: FakeImportJobRepository,
    process_file: Any,
    *,
    file_sink: RecordingFileStatusSink | None = None,
    kb_sink: RecordingKbCountSink | None = None,
) -> ImportJobWorker:
    return ImportJobWorker(
        jobs=repo,  # type: ignore[arg-type]
        process_file=process_file,
        file_status_sink=file_sink,
        kb_count_sink=kb_sink,
    )


def test_run_job_all_files_success() -> None:
    repo = FakeImportJobRepository()
    job = _make_job()
    repo.seed(job)
    file_sink = RecordingFileStatusSink()
    kb_sink = RecordingKbCountSink()

    def process_file(
        *,
        job: ImportJob,
        file_id: str,
        file_index: int,
        progress: object,
    ) -> int:
        del job, file_index, progress
        return {"file_a": 3, "file_b": 5}[file_id]

    result = _worker(repo, process_file, file_sink=file_sink, kb_sink=kb_sink).run_job(
        job.id
    )

    assert result.status == ImportJobStatus.COMPLETED
    assert result.stage == ImportJobStage.DONE
    assert result.progress == 100
    assert kb_sink.calls == [
        {"kb_id": "kb_test", "file_count_delta": 2, "chunk_count_delta": 8}
    ]

    completed = [
        m
        for m in file_sink.marks
        if m.status == ImportJobFileStatus.COMPLETED
    ]
    assert {m.file_id for m in completed} == {"file_a", "file_b"}
    assert all(m.kb_id == "kb_test" for m in completed)


def test_run_job_partial_file_failure() -> None:
    repo = FakeImportJobRepository()
    job = _make_job(file_ids=["file_ok", "file_bad", "file_ok2"])
    repo.seed(job)
    file_sink = RecordingFileStatusSink()
    kb_sink = RecordingKbCountSink()

    def process_file(
        *,
        job: ImportJob,
        file_id: str,
        file_index: int,
        progress: object,
    ) -> int:
        del job, file_index, progress
        if file_id == "file_bad":
            raise RuntimeError("parse failed")
        return 2

    result = _worker(repo, process_file, file_sink=file_sink, kb_sink=kb_sink).run_job(
        job.id
    )

    assert result.status == ImportJobStatus.FAILED
    assert result.error_code == "IMPORT_FILE_FAILED"
    assert result.error_message == "parse failed"

    failed = [m for m in file_sink.marks if m.status == ImportJobFileStatus.FAILED]
    assert len(failed) == 1
    assert failed[0].file_id == "file_bad"
    assert failed[0].error == "parse failed"

    completed = [
        m for m in file_sink.marks if m.status == ImportJobFileStatus.COMPLETED
    ]
    assert {m.file_id for m in completed} == {"file_ok", "file_ok2"}

    assert kb_sink.calls == [
        {"kb_id": "kb_test", "file_count_delta": 2, "chunk_count_delta": 4}
    ]


def test_run_job_terminal_is_idempotent() -> None:
    repo = FakeImportJobRepository()
    job = _make_job(
        status=ImportJobStatus.COMPLETED,
        stage=ImportJobStage.DONE,
        progress=100,
    )
    repo.seed(job)

    def process_file(
        *,
        job: ImportJob,
        file_id: str,
        file_index: int,
        progress: object,
    ) -> int:
        del job, file_id, file_index, progress
        raise AssertionError("不应处理终态任务")

    result = _worker(repo, process_file).run_job(job.id)

    assert result.status == ImportJobStatus.COMPLETED
    assert repo.update_calls == []


def test_run_job_progress_and_transitions_are_valid() -> None:
    repo = FakeImportJobRepository()
    job = _make_job(file_ids=["f1", "f2", "f3"])
    repo.seed(job)

    def process_file(
        *,
        job: ImportJob,
        file_id: str,
        file_index: int,
        progress: object,
    ) -> int:
        del job, file_id, file_index, progress
        return 1

    _worker(repo, process_file).run_job(job.id)

    statuses_seen: list[ImportJobStatus] = [ImportJobStatus.QUEUED]
    progress_values: list[int] = [0]

    for call in repo.update_calls:
        if call.status is not None:
            prev = statuses_seen[-1]
            assert call.status in ALLOWED_STATUS_TRANSITIONS.get(prev, frozenset())
            statuses_seen.append(call.status)
        if call.progress is not None:
            progress_values.append(call.progress)

    assert statuses_seen[-1] == ImportJobStatus.COMPLETED
    assert progress_values == sorted(progress_values)
    assert progress_values[-1] == 100


def test_run_job_missing_raises() -> None:
    repo = FakeImportJobRepository()
    worker = _worker(repo, lambda **_: 1)

    with pytest.raises(ValueError, match="导入任务不存在"):
        worker.run_job("missing_job")
