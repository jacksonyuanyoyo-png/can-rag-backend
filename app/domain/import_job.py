from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class ImportJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ImportJobStage(StrEnum):
    UPLOAD = "upload"
    PARSE = "parse"
    CHUNK = "chunk"
    EMBED = "embed"
    INDEX = "index"
    DONE = "done"


class ImportJobFileStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


TERMINAL_STATUSES: frozenset[ImportJobStatus] = frozenset(
    {
        ImportJobStatus.COMPLETED,
        ImportJobStatus.FAILED,
        ImportJobStatus.CANCELLED,
    }
)

CANCELLABLE_STATUSES: frozenset[ImportJobStatus] = frozenset(
    {
        ImportJobStatus.QUEUED,
        ImportJobStatus.RUNNING,
    }
)

RETRYABLE_STATUSES: frozenset[ImportJobStatus] = frozenset(
    {
        ImportJobStatus.FAILED,
        ImportJobStatus.CANCELLED,
    }
)

ALLOWED_STATUS_TRANSITIONS: dict[ImportJobStatus, frozenset[ImportJobStatus]] = {
    ImportJobStatus.QUEUED: frozenset({ImportJobStatus.RUNNING, ImportJobStatus.CANCELLED}),
    ImportJobStatus.RUNNING: frozenset(
        {
            ImportJobStatus.COMPLETED,
            ImportJobStatus.FAILED,
            ImportJobStatus.CANCELLED,
        }
    ),
    ImportJobStatus.COMPLETED: frozenset(),
    ImportJobStatus.FAILED: frozenset(),
    ImportJobStatus.CANCELLED: frozenset(),
}

STAGE_ORDER: tuple[ImportJobStage, ...] = (
    ImportJobStage.UPLOAD,
    ImportJobStage.PARSE,
    ImportJobStage.CHUNK,
    ImportJobStage.EMBED,
    ImportJobStage.INDEX,
    ImportJobStage.DONE,
)


class ImportJobTransitionError(ValueError):
    def __init__(
        self,
        *,
        current_status: ImportJobStatus,
        target_status: ImportJobStatus | None = None,
        message: str | None = None,
    ) -> None:
        self.current_status = current_status
        self.target_status = target_status
        detail = message or (
            f"导入任务状态不允许从 {current_status.value} 转换"
            + (f" 到 {target_status.value}" if target_status is not None else "")
        )
        super().__init__(detail)


def validate_status_transition(
    current: ImportJobStatus,
    target: ImportJobStatus,
) -> None:
    if current == target:
        return
    allowed = ALLOWED_STATUS_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise ImportJobTransitionError(current_status=current, target_status=target)


def validate_stage_transition(
    current: ImportJobStage,
    target: ImportJobStage,
) -> None:
    if current == target:
        return
    current_index = STAGE_ORDER.index(current)
    target_index = STAGE_ORDER.index(target)
    if target_index < current_index:
        raise ValueError(
            f"导入任务 stage 不允许从 {current.value} 回退到 {target.value}"
        )


def utc_now() -> datetime:
    return datetime.now(UTC)


def _enum_value(value: Any) -> Any:
    if hasattr(value, "value"):
        return value.value
    return value


def _read_attr(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    return getattr(obj, name, default)


@dataclass(frozen=True, slots=True)
class ParsingConfig:
    text_extraction: bool = True
    pdf_enhancement: bool = False

    @classmethod
    def default(cls) -> ParsingConfig:
        return cls()

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ParsingConfig:
        if not data:
            return cls.default()
        return cls(
            text_extraction=bool(data.get("textExtraction", True)),
            pdf_enhancement=bool(data.get("pdfEnhancement", False)),
        )

    def to_dict(self) -> dict[str, bool]:
        return {
            "textExtraction": self.text_extraction,
            "pdfEnhancement": self.pdf_enhancement,
        }


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    strategy: str
    mode: str | None = None
    chunk_size: int | None = None
    overlap: int | None = None
    max_chunk_size: int | None = None
    separators: list[str] | None = None
    paragraph_use_model: bool = False
    paragraph_max_depth: int | None = None
    index_size: int = 512
    meta_filename: bool = True
    meta_headings: bool = False
    parsing: ParsingConfig = field(default_factory=ParsingConfig.default)

    @classmethod
    def default(
        cls,
        strategy: str,
        *,
        meta_filename: bool,
        meta_headings: bool,
        parsing: ParsingConfig | None = None,
    ) -> ChunkingConfig:
        return cls(
            strategy=strategy,
            index_size=512,
            meta_filename=meta_filename,
            meta_headings=meta_headings,
            parsing=parsing or ParsingConfig.default(),
        )

    @classmethod
    def from_chunking_options(
        cls,
        options: Any,
        *,
        fallback_strategy: str,
        metadata: Any,
        parsing: Any = None,
    ) -> ChunkingConfig:
        meta_filename = True
        meta_headings = False
        nested_meta = _read_attr(options, "metadata") if options is not None else None
        if nested_meta is not None:
            meta_filename = bool(_read_attr(nested_meta, "include_file_name", True))
            meta_headings = bool(_read_attr(nested_meta, "include_headings", False))
        elif metadata is not None:
            meta_filename = bool(_read_attr(metadata, "include_file_name", True))
            meta_headings = bool(_read_attr(metadata, "include_headings", False))

        parsing_config = _parsing_config_from_input(parsing)

        if options is None:
            return cls.default(
                fallback_strategy,
                meta_filename=meta_filename,
                meta_headings=meta_headings,
                parsing=parsing_config,
            )

        strategy = str(_enum_value(_read_attr(options, "strategy", fallback_strategy)))
        index_size_raw = _read_attr(options, "index_size")
        index_size = 512 if index_size_raw is None else int(index_size_raw)

        custom = _read_attr(options, "custom")
        mode = (
            str(_enum_value(_read_attr(custom, "mode")))
            if custom is not None
            else None
        )

        paragraph = _read_attr(options, "paragraph")
        length = _read_attr(options, "length")
        separator = _read_attr(options, "separator")

        chunk_size: int | None = None
        overlap: int | None = None
        max_chunk_size: int | None = None
        separators: list[str] | None = None
        paragraph_use_model = False
        paragraph_max_depth: int | None = None

        if paragraph is not None:
            paragraph_use_model = bool(_read_attr(paragraph, "use_model", False))
            paragraph_max_depth = _read_attr(paragraph, "max_depth")
            if paragraph_max_depth is not None:
                paragraph_max_depth = int(paragraph_max_depth)

        if length is not None:
            chunk_size = int(_read_attr(length, "chunk_size"))
            overlap = int(_read_attr(length, "overlap"))
            max_chunk_size = int(_read_attr(length, "max_chunk_size"))

        if separator is not None:
            raw_separators = _read_attr(separator, "separators")
            separators = list(raw_separators) if raw_separators is not None else None

        return cls(
            strategy=strategy,
            mode=mode,
            chunk_size=chunk_size,
            overlap=overlap,
            max_chunk_size=max_chunk_size,
            separators=separators,
            paragraph_use_model=paragraph_use_model,
            paragraph_max_depth=paragraph_max_depth,
            index_size=index_size,
            meta_filename=meta_filename,
            meta_headings=meta_headings,
            parsing=parsing_config,
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "strategy": self.strategy,
            "indexSize": self.index_size,
            "metaFilename": self.meta_filename,
            "metaHeadings": self.meta_headings,
            "paragraphUseModel": self.paragraph_use_model,
        }
        if self.mode is not None:
            payload["mode"] = self.mode
        if self.chunk_size is not None:
            payload["chunkSize"] = self.chunk_size
        if self.overlap is not None:
            payload["overlap"] = self.overlap
        if self.max_chunk_size is not None:
            payload["maxChunkSize"] = self.max_chunk_size
        if self.separators is not None:
            payload["separators"] = list(self.separators)
        if self.paragraph_max_depth is not None:
            payload["paragraphMaxDepth"] = self.paragraph_max_depth
        payload["parsing"] = self.parsing.to_dict()
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChunkingConfig:
        separators = data.get("separators")
        return cls(
            strategy=str(data["strategy"]),
            mode=data.get("mode"),
            chunk_size=data.get("chunkSize"),
            overlap=data.get("overlap"),
            max_chunk_size=data.get("maxChunkSize"),
            separators=list(separators) if separators is not None else None,
            paragraph_use_model=bool(data.get("paragraphUseModel", False)),
            paragraph_max_depth=data.get("paragraphMaxDepth"),
            index_size=int(data.get("indexSize", 512)),
            meta_filename=bool(data.get("metaFilename", True)),
            meta_headings=bool(data.get("metaHeadings", False)),
            parsing=ParsingConfig.from_dict(data.get("parsing")),
        )


def _parsing_config_from_input(parsing: Any) -> ParsingConfig:
    if parsing is None:
        return ParsingConfig.default()
    if isinstance(parsing, ParsingConfig):
        return parsing
    if isinstance(parsing, dict):
        return ParsingConfig.from_dict(parsing)
    return ParsingConfig(
        text_extraction=bool(_read_attr(parsing, "text_extraction", True)),
        pdf_enhancement=bool(_read_attr(parsing, "pdf_enhancement", False)),
    )


@dataclass(slots=True)
class ImportJobOption:
    chunk_strategy: str
    meta_filename: bool = True
    meta_headings: bool = False


@dataclass(slots=True)
class ImportJobFile:
    id: str
    import_job_id: str
    file_id: str
    file_status: ImportJobFileStatus = ImportJobFileStatus.PENDING
    error_code: str | None = None


@dataclass(slots=True)
class ImportJob:
    id: str
    kb_id: str
    file_ids: list[str]
    status: ImportJobStatus
    progress: int
    stage: ImportJobStage
    error_code: str | None = None
    error_message: str | None = None
    retry_of: str | None = None
    option: ImportJobOption | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "knowledgeBaseId": self.kb_id,
            "fileIds": list(self.file_ids),
            "status": self.status.value,
            "progress": self.progress,
            "stage": self.stage.value,
            "errorCode": self.error_code,
            "errorMessage": self.error_message,
            "retryOf": self.retry_of,
            "createdAt": self.created_at.isoformat(),
            "updatedAt": self.updated_at.isoformat(),
        }

    @classmethod
    def from_row(
        cls,
        row: dict[str, Any],
        *,
        file_ids: list[str] | None = None,
        option: ImportJobOption | None = None,
    ) -> ImportJob:
        return cls(
            id=str(row["id"]),
            kb_id=str(row["kb_id"]),
            file_ids=list(file_ids or []),
            status=ImportJobStatus(str(row["status"])),
            progress=int(row["progress"]),
            stage=ImportJobStage(str(row["stage"])),
            error_code=row.get("error_code"),
            error_message=row.get("error_message"),
            retry_of=row.get("retry_of"),
            option=option,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
