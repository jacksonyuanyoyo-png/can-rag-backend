from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Callable, Protocol

import psycopg
from psycopg.rows import dict_row

from app.core.config import Settings
from app.core.database import normalize_psycopg_url
from app.domain.import_job import ChunkingConfig, ImportJob, ImportJobFileStatus, ImportJobStage
from app.services.import_job_progress import ImportJobProgressReporter
from app.repositories.import_job_repository import ImportJobRepository
from app.repositories.knowledge_base_repository import KnowledgeBaseRepository
from app.services.import_job_worker import FileProcessor, ImportJobWorker
from app.services.rag.kb_embedding import resolve_kb_embedding_config
from app.services.rag.parsing import get_parser_for
from app.services.rag.parsing.docx_parser import DocxDocumentParser
from app.services.rag.parsing.md_parser import (
    MarkdownDocumentParser,
    extract_image_storage_keys,
)
from app.services.rag.parsing.image_store import ImageStore
from app.services.rag.parsing.pdf_to_markdown import parse_pdf_with_options
from app.services.rag.pipeline import RagPipeline
from app.services.rag.vlm_service import VlmService

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 5.0

_DEFAULT_CHUNKING = ChunkingConfig.from_dict({"strategy": "default"})

_STATUS_TO_DB: dict[ImportJobFileStatus, str] = {
    ImportJobFileStatus.RUNNING: "indexing",
    ImportJobFileStatus.COMPLETED: "ready",
    ImportJobFileStatus.FAILED: "failed",
}

QueuedJobIdsFetcher = Callable[[], list[str]]


class FileResolver(Protocol):
    def __call__(self, *, kb_id: str, file_id: str) -> tuple[str, Path]: ...


class KbFileNotFoundError(FileNotFoundError):
    pass


def _resolve_storage_path(settings: Settings, storage_key: str) -> Path:
    relative = Path(storage_key)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"非法 storage_key: {storage_key}")
    return settings.upload_root_resolved / relative


class KbFileResolver:
    def __init__(self, settings: Settings) -> None:
        if not settings.DATABASE_URL.strip():
            raise ValueError("KbFileResolver 需要配置 DATABASE_URL。")
        self._settings = settings
        self._dsn = normalize_psycopg_url(settings.DATABASE_URL)

    def __call__(self, *, kb_id: str, file_id: str) -> tuple[str, Path]:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT file_name, storage_key, kb_id
                    FROM app.t_dim_kb_file
                    WHERE id = %s
                    """,
                    (file_id,),
                )
                row = cur.fetchone()
        if row is None:
            raise KbFileNotFoundError(
                f"知识库文件不存在: kb_id={kb_id}, file_id={file_id}"
            )
        if str(row["kb_id"]) != kb_id:
            raise KbFileNotFoundError(
                f"文件 {file_id} 不属于知识库 {kb_id}"
            )
        file_name = str(row["file_name"])
        storage_key = str(row["storage_key"])
        path = _resolve_storage_path(self._settings, storage_key)
        if not path.exists():
            raise FileNotFoundError(
                f"文件未找到本地路径: {path} (file_id={file_id})"
            )
        return file_name, path


def build_process_file(
    *,
    pipeline: RagPipeline,
    resolver: FileResolver,
    jobs: ImportJobRepository,
    kb_repository: KnowledgeBaseRepository,
    settings: Settings,
) -> FileProcessor:
    vlm_service = VlmService(settings)
    image_store = ImageStore(settings.upload_root_resolved)
    upload_root = settings.upload_root_resolved

    def process_file(
        *,
        job: ImportJob,
        file_id: str,
        file_index: int,
        progress: ImportJobProgressReporter,
    ) -> int:
        file_name, path = resolver(kb_id=job.kb_id, file_id=file_id)
        cfg_dict = jobs.get_chunking_config(job.id)
        config = (
            ChunkingConfig.from_dict(cfg_dict)
            if cfg_dict
            else _DEFAULT_CHUNKING
        )

        def on_pdf_page(current_page: int, total_pages: int) -> None:
            progress.report_parse_page(
                file_index=file_index,
                current_page=current_page,
                total_pages=total_pages,
            )

        progress.report_stage(
            ImportJobStage.PARSE,
            file_index=file_index,
            fraction=0.0,
            force=True,
        )
        if file_name.lower().endswith(".pdf"):
            document = parse_pdf_with_options(
                path,
                text_extraction=config.parsing.text_extraction,
                pdf_enhancement=config.parsing.pdf_enhancement,
                settings=settings,
                vlm_service=vlm_service,
                image_store=image_store,
                on_page_progress=on_pdf_page,
            )
            if config.parsing.pdf_enhancement:
                config = ChunkingConfig.from_dict(
                    {**config.to_dict(), "metaHeadings": True}
                )
        elif file_name.lower().endswith((".md", ".markdown")):
            progress.report_stage(
                ImportJobStage.PARSE,
                file_index=file_index,
                fraction=0.25,
            )
            document = MarkdownDocumentParser(
                image_store=image_store,
                upload_root=upload_root,
            ).parse(path)
            if document.blocks and any(block.heading for block in document.blocks):
                config = ChunkingConfig.from_dict(
                    {**config.to_dict(), "metaHeadings": True}
                )
        elif file_name.lower().endswith(".docx"):
            progress.report_stage(
                ImportJobStage.PARSE,
                file_index=file_index,
                fraction=0.25,
            )
            document = DocxDocumentParser(image_store=image_store).parse(path)
            if document.blocks and any(block.heading for block in document.blocks):
                config = ChunkingConfig.from_dict(
                    {**config.to_dict(), "metaHeadings": True}
                )
        else:
            parser = get_parser_for(file_name)
            if parser is None:
                raise ValueError(f"不支持的文件类型: {file_name}")
            progress.report_stage(
                ImportJobStage.PARSE,
                file_index=file_index,
                fraction=0.5,
            )
            document = parser.parse(path)
        progress.report_stage(
            ImportJobStage.PARSE,
            file_index=file_index,
            fraction=1.0,
            force=True,
        )

        metadata = kb_repository.get_by_id(job.kb_id)
        embedding_config = resolve_kb_embedding_config(settings, metadata)

        def on_pipeline_progress(stage: ImportJobStage, fraction: float) -> None:
            progress.report_stage(stage, file_index=file_index, fraction=fraction)

        has_figures = bool(document.images) or bool(
            extract_image_storage_keys(document.full_text)
        )
        is_pdf = file_name.lower().endswith(".pdf")
        is_docx = file_name.lower().endswith(".docx")
        # DOCX/PDF 增强：正文已含页图 Markdown，不再对每张图单独 VLM（避免 jp2 与重复段）
        pdf_enhanced = is_pdf and config.parsing.pdf_enhancement
        force_image_description = (not is_docx and not pdf_enhanced) and (
            config.parsing.image_vlm_index and has_figures
        )

        result = pipeline.index_data(
            knowledge_base=job.kb_id,
            file_id=file_id,
            document=document,
            config=config,
            file_name=file_name,
            embedding_config=embedding_config,
            force_image_description=force_image_description,
            on_progress=on_pipeline_progress,
        )
        return int(result["data"])

    return process_file


class KbFileStatusSink:
    def __init__(self, settings: Settings) -> None:
        self._dsn = normalize_psycopg_url(settings.DATABASE_URL)

    def mark_file(
        self,
        *,
        kb_id: str,
        file_id: str,
        status: ImportJobFileStatus,
        error: str | None = None,
    ) -> None:
        db_status = _STATUS_TO_DB.get(status)
        if db_status is None:
            return
        try:
            with psycopg.connect(
                self._dsn,
                autocommit=True,
                row_factory=dict_row,
            ) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE app.t_dim_kb_file
                        SET status = %s,
                            error_message = %s,
                            updated_at = now()
                        WHERE id = %s AND kb_id = %s
                        """,
                        (db_status, error, file_id, kb_id),
                    )
        except Exception:
            logger.exception(
                "更新 kb_file 状态失败: kb_id=%s file_id=%s status=%s",
                kb_id,
                file_id,
                status,
            )


class KbCountSink:
    def __init__(self, settings: Settings) -> None:
        self._dsn = normalize_psycopg_url(settings.DATABASE_URL)

    def add_counts(
        self,
        *,
        kb_id: str,
        file_count_delta: int,
        chunk_count_delta: int,
    ) -> None:
        if file_count_delta == 0 and chunk_count_delta == 0:
            return
        try:
            with psycopg.connect(
                self._dsn,
                autocommit=True,
                row_factory=dict_row,
            ) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE app.t_dim_knowledge_base
                        SET file_count = file_count + %s,
                            chunk_count = chunk_count + %s,
                            updated_at = now()
                        WHERE id = %s
                        """,
                        (file_count_delta, chunk_count_delta, kb_id),
                    )
        except Exception:
            logger.exception(
                "更新 kb 计数失败: kb_id=%s file_delta=%s chunk_delta=%s",
                kb_id,
                file_count_delta,
                chunk_count_delta,
            )


def fetch_queued_job_ids(database_url: str) -> list[str]:
    dsn = normalize_psycopg_url(database_url)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id
                FROM app.t_fact_import_job
                WHERE status = %s
                ORDER BY created_at ASC
                """,
                ("queued",),
            )
            rows = cur.fetchall() or []
    return [str(row["id"]) for row in rows]


class ImportJobPoller:
    def __init__(
        self,
        *,
        worker: ImportJobWorker,
        database_url: str,
        poll_interval_seconds: float = POLL_INTERVAL_SECONDS,
        fetch_queued_job_ids_fn: QueuedJobIdsFetcher | None = None,
    ) -> None:
        self._worker = worker
        self._poll_interval = poll_interval_seconds
        self._fetch_queued = fetch_queued_job_ids_fn or (
            lambda: fetch_queued_job_ids(database_url)
        )
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    def poll_once(self) -> None:
        for job_id in self._fetch_queued():
            try:
                self._worker.run_job(job_id)
            except Exception:
                logger.exception("轮询执行导入任务失败: job_id=%s", job_id)

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop(), name="import-job-poller")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    await asyncio.to_thread(self.poll_once)
                except Exception:
                    logger.exception("导入任务轮询周期异常")
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._poll_interval,
                    )
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise
