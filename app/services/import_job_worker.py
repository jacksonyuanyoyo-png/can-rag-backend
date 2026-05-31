from __future__ import annotations

from typing import Protocol

from app.domain.import_job import (
    ImportJob,
    ImportJobFileStatus,
    ImportJobStage,
    ImportJobStatus,
    TERMINAL_STATUSES,
)
from app.repositories.import_job_repository import ImportJobRepository
from app.services.import_job_progress import (
    ImportJobProgressReporter,
    ImportJobProgressTracker,
)


class FileProcessor(Protocol):
    def __call__(
        self,
        *,
        job: ImportJob,
        file_id: str,
        file_index: int,
        progress: ImportJobProgressReporter,
    ) -> int: ...


class FileStatusSink(Protocol):
    def mark_file(
        self,
        *,
        kb_id: str,
        file_id: str,
        status: ImportJobFileStatus,
        error: str | None = None,
    ) -> None: ...


class KbCountSink(Protocol):
    def add_counts(
        self,
        *,
        kb_id: str,
        file_count_delta: int,
        chunk_count_delta: int,
    ) -> None: ...


class NullFileStatusSink:
    def mark_file(
        self,
        *,
        kb_id: str,
        file_id: str,
        status: ImportJobFileStatus,
        error: str | None = None,
    ) -> None:
        pass


class NullKbCountSink:
    def add_counts(
        self,
        *,
        kb_id: str,
        file_count_delta: int,
        chunk_count_delta: int,
    ) -> None:
        pass


class ImportJobWorker:
    def __init__(
        self,
        *,
        jobs: ImportJobRepository,
        process_file: FileProcessor,
        file_status_sink: FileStatusSink | None = None,
        kb_count_sink: KbCountSink | None = None,
    ) -> None:
        self._jobs = jobs
        self._process_file = process_file
        self._file_status_sink = file_status_sink or NullFileStatusSink()
        self._kb_count_sink = kb_count_sink or NullKbCountSink()

    def run_job(self, job_id: str) -> ImportJob:
        job = self._jobs.get(job_id)
        if job is None:
            raise ValueError(f"导入任务不存在: {job_id}")

        if job.status in TERMINAL_STATUSES:
            return job

        tracker = ImportJobProgressTracker(
            self._jobs,
            job_id,
            file_count=len(job.file_ids),
        )
        if job.status == ImportJobStatus.QUEUED:
            tracker.report_stage(
                ImportJobStage.UPLOAD,
                file_index=0,
                fraction=1.0,
                force=True,
            )
            tracker.mark_running()
            job = self._require_updated(self._jobs.get(job_id), job_id)

        done_files = 0
        total_chunks = 0
        first_error: str | None = None

        for file_index, file_id in enumerate(job.file_ids):
            self._file_status_sink.mark_file(
                kb_id=job.kb_id,
                file_id=file_id,
                status=ImportJobFileStatus.RUNNING,
            )
            try:
                chunk_count = self._process_file(
                    job=job,
                    file_id=file_id,
                    file_index=file_index,
                    progress=tracker,
                )
                total_chunks += chunk_count
                done_files += 1
                tracker.report_stage(
                    ImportJobStage.INDEX,
                    file_index=file_index,
                    fraction=1.0,
                    force=True,
                )
                self._file_status_sink.mark_file(
                    kb_id=job.kb_id,
                    file_id=file_id,
                    status=ImportJobFileStatus.COMPLETED,
                )
            except Exception as exc:
                if first_error is None:
                    first_error = str(exc)
                self._file_status_sink.mark_file(
                    kb_id=job.kb_id,
                    file_id=file_id,
                    status=ImportJobFileStatus.FAILED,
                    error=str(exc),
                )

        if first_error is None:
            tracker.mark_completed()
            self._kb_count_sink.add_counts(
                kb_id=job.kb_id,
                file_count_delta=done_files,
                chunk_count_delta=total_chunks,
            )
        else:
            tracker.mark_failed(
                error_code="IMPORT_FILE_FAILED",
                error_message=first_error,
            )
            if done_files > 0 or total_chunks > 0:
                self._kb_count_sink.add_counts(
                    kb_id=job.kb_id,
                    file_count_delta=done_files,
                    chunk_count_delta=total_chunks,
                )

        final = self._jobs.get(job_id)
        if final is None:
            raise ValueError(f"导入任务不存在: {job_id}")
        return final

    @staticmethod
    def _require_updated(updated: ImportJob | None, job_id: str) -> ImportJob:
        if updated is None:
            raise ValueError(f"导入任务不存在: {job_id}")
        return updated
