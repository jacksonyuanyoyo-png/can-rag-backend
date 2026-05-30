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


class FileProcessor(Protocol):
    def __call__(self, *, job: ImportJob, file_id: str) -> int: ...


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


_PROCESSING_STAGES: tuple[ImportJobStage, ...] = (
    ImportJobStage.CHUNK,
    ImportJobStage.EMBED,
    ImportJobStage.INDEX,
)


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

        if job.status == ImportJobStatus.QUEUED:
            job = self._require_updated(
                self._jobs.update_progress(
                    job_id,
                    status=ImportJobStatus.RUNNING,
                    stage=ImportJobStage.PARSE,
                    progress=0,
                    clear_error=True,
                ),
                job_id,
            )

        for stage in _PROCESSING_STAGES:
            job = self._require_updated(
                self._jobs.update_progress(job_id, stage=stage),
                job_id,
            )

        total_files = len(job.file_ids)
        done_files = 0
        total_chunks = 0
        first_error: str | None = None
        processed = 0

        for file_id in job.file_ids:
            self._file_status_sink.mark_file(
                kb_id=job.kb_id,
                file_id=file_id,
                status=ImportJobFileStatus.RUNNING,
            )
            try:
                chunk_count = self._process_file(job=job, file_id=file_id)
                total_chunks += chunk_count
                done_files += 1
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

            processed += 1
            if total_files > 0:
                progress = min(100, (processed * 100) // total_files)
                job = self._require_updated(
                    self._jobs.update_progress(job_id, progress=progress),
                    job_id,
                )

        if first_error is None:
            self._jobs.update_progress(
                job_id,
                status=ImportJobStatus.COMPLETED,
                stage=ImportJobStage.DONE,
                progress=100,
            )
            self._kb_count_sink.add_counts(
                kb_id=job.kb_id,
                file_count_delta=done_files,
                chunk_count_delta=total_chunks,
            )
        else:
            self._jobs.update_progress(
                job_id,
                status=ImportJobStatus.FAILED,
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
