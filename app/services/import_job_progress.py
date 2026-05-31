from __future__ import annotations

import time
from typing import Protocol

from app.domain.import_job import ImportJobStage, ImportJobStatus
from app.repositories.import_job_repository import ImportJobRepository

STAGE_PROGRESS_RANGES: dict[ImportJobStage, tuple[int, int]] = {
    ImportJobStage.UPLOAD: (0, 10),
    ImportJobStage.PARSE: (10, 35),
    ImportJobStage.CHUNK: (35, 55),
    ImportJobStage.EMBED: (55, 80),
    ImportJobStage.INDEX: (80, 99),
    ImportJobStage.DONE: (100, 100),
}

MIN_PROGRESS_UPDATE_INTERVAL_SECONDS = 1.0


def progress_for_stage(
    stage: ImportJobStage,
    *,
    file_index: int,
    file_count: int,
    fraction: float,
) -> int:
    """将 stage 内进度 (0–1) 与多文件序号映射为 0–100 整数。"""
    if file_count <= 0:
        raise ValueError("file_count 必须大于 0")
    low, high = STAGE_PROGRESS_RANGES[stage]
    if stage == ImportJobStage.DONE:
        return 100
    clamped = max(0.0, min(1.0, fraction))
    span = high - low
    slot = (file_index + clamped) / file_count
    value = low + span * slot
    return max(0, min(99 if stage != ImportJobStage.DONE else 100, int(value)))


def parse_page_progress(current_page: int, total_pages: int) -> float:
    if total_pages <= 0:
        return 1.0
    return max(0.0, min(1.0, current_page / total_pages))


class ImportJobProgressReporter(Protocol):
    def report_stage(
        self,
        stage: ImportJobStage,
        *,
        file_index: int,
        fraction: float = 0.0,
        force: bool = False,
    ) -> None: ...

    def report_parse_page(
        self,
        *,
        file_index: int,
        current_page: int,
        total_pages: int,
        force: bool = False,
    ) -> None: ...


class ImportJobProgressTracker:
    """导入任务进度写入：stage 区间映射、单调递增、轮询友好的节流。"""

    def __init__(
        self,
        jobs: ImportJobRepository,
        job_id: str,
        *,
        file_count: int,
        min_update_interval_seconds: float = MIN_PROGRESS_UPDATE_INTERVAL_SECONDS,
    ) -> None:
        self._jobs = jobs
        self._job_id = job_id
        self._file_count = max(1, file_count)
        self._min_interval = min_update_interval_seconds
        self._last_progress = -1
        self._last_stage: ImportJobStage | None = None
        self._last_update_at = 0.0

    def report_stage(
        self,
        stage: ImportJobStage,
        *,
        file_index: int,
        fraction: float = 0.0,
        force: bool = False,
    ) -> None:
        progress = progress_for_stage(
            stage,
            file_index=file_index,
            file_count=self._file_count,
            fraction=fraction,
        )
        self._persist(stage=stage, progress=progress, force=force)

    def report_parse_page(
        self,
        *,
        file_index: int,
        current_page: int,
        total_pages: int,
        force: bool = False,
    ) -> None:
        fraction = parse_page_progress(current_page, total_pages)
        self.report_stage(
            ImportJobStage.PARSE,
            file_index=file_index,
            fraction=fraction,
            force=force,
        )

    def mark_running(self) -> None:
        self._persist(
            status=ImportJobStatus.RUNNING,
            stage=ImportJobStage.PARSE,
            progress=progress_for_stage(
                ImportJobStage.PARSE,
                file_index=0,
                file_count=self._file_count,
                fraction=0.0,
            ),
            force=True,
            clear_error=True,
        )

    def mark_completed(self) -> None:
        self._persist(
            status=ImportJobStatus.COMPLETED,
            stage=ImportJobStage.DONE,
            progress=100,
            force=True,
        )

    def mark_failed(self, *, error_code: str, error_message: str) -> None:
        self._jobs.update_progress(
            self._job_id,
            status=ImportJobStatus.FAILED,
            error_code=error_code,
            error_message=error_message,
        )

    def _persist(
        self,
        *,
        status: ImportJobStatus | None = None,
        stage: ImportJobStage | None = None,
        progress: int | None = None,
        force: bool = False,
        clear_error: bool = False,
    ) -> None:
        if progress is not None:
            if progress <= self._last_progress and not force:
                if stage is None or stage == self._last_stage:
                    return
            progress = max(self._last_progress, progress)
            self._last_progress = progress

        now = time.monotonic()
        stage_changed = stage is not None and stage != self._last_stage
        if not force and not stage_changed and status is None:
            if now - self._last_update_at < self._min_interval:
                return

        self._jobs.update_progress(
            self._job_id,
            status=status,
            progress=progress,
            stage=stage,
            clear_error=clear_error,
        )
        self._last_update_at = now
        if stage is not None:
            self._last_stage = stage


class NullImportJobProgressReporter:
    def report_stage(
        self,
        stage: ImportJobStage,
        *,
        file_index: int,
        fraction: float = 0.0,
        force: bool = False,
    ) -> None:
        del stage, file_index, fraction, force

    def report_parse_page(
        self,
        *,
        file_index: int,
        current_page: int,
        total_pages: int,
        force: bool = False,
    ) -> None:
        del file_index, current_page, total_pages, force
