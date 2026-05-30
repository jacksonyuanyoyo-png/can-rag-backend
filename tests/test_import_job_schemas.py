from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.schemas.import_job import (
    INVALID_OPTIONS_CODE,
    ChunkStrategy,
    CreateImportJobRequest,
    RetryImportJobRequest,
)


def _create(payload: dict) -> CreateImportJobRequest:
    return CreateImportJobRequest.model_validate({"fileIds": ["f1"], **payload})


def test_default_strategy_parses() -> None:
    req = _create({"chunking": {"strategy": "default"}})

    assert req.chunking is not None
    assert req.chunking.strategy == ChunkStrategy.DEFAULT
    assert req.chunk_strategy == "default"


def test_page_strategy_parses() -> None:
    req = _create({"chunking": {"strategy": "page", "indexSize": 512}})

    assert req.chunking is not None
    assert req.chunking.strategy == ChunkStrategy.PAGE
    assert int(req.chunking.index_size) == 512
    assert req.chunk_strategy == "page"


def test_custom_paragraph_parses() -> None:
    req = _create(
        {
            "chunking": {
                "strategy": "custom",
                "custom": {"mode": "paragraph"},
                "paragraph": {"useModel": True, "maxDepth": 3},
            }
        }
    )

    assert req.chunking is not None
    assert req.chunking.paragraph is not None
    assert req.chunking.paragraph.use_model is True
    assert req.chunking.paragraph.max_depth == 3
    assert req.chunk_strategy == "custom"


def test_custom_length_parses() -> None:
    req = _create(
        {
            "chunking": {
                "strategy": "custom",
                "custom": {"mode": "length"},
                "length": {"chunkSize": 512, "overlap": 50, "maxChunkSize": 1024},
                "indexSize": 512,
            }
        }
    )

    assert req.chunking is not None
    assert req.chunking.length is not None
    assert req.chunking.length.chunk_size == 512
    assert req.chunking.length.overlap == 50
    assert req.chunking.length.max_chunk_size == 1024


def test_custom_separator_parses() -> None:
    req = _create(
        {
            "chunking": {
                "strategy": "custom",
                "custom": {"mode": "separator"},
                "separator": {"separators": ["\n\n", "##"]},
            }
        }
    )

    assert req.chunking is not None
    assert req.chunking.separator is not None
    assert req.chunking.separator.separators == ["\n\n", "##"]


def test_metadata_nested_in_chunking_overrides() -> None:
    req = _create(
        {
            "chunking": {
                "strategy": "default",
                "metadata": {"includeFileName": False, "includeHeadings": True},
            }
        }
    )

    assert req.metadata.include_file_name is False
    assert req.metadata.include_headings is True


def test_whole_strategy_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _create({"chunking": {"strategy": "whole"}})

    assert INVALID_OPTIONS_CODE in str(exc_info.value)


def test_index_size_invalid_value_rejected() -> None:
    with pytest.raises(ValidationError):
        _create({"chunking": {"strategy": "default", "indexSize": 999}})


def test_index_size_must_not_exceed_max_chunk_size() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _create(
            {
                "chunking": {
                    "strategy": "custom",
                    "custom": {"mode": "length"},
                    "length": {"chunkSize": 256, "overlap": 0, "maxChunkSize": 256},
                    "indexSize": 512,
                }
            }
        )

    assert INVALID_OPTIONS_CODE in str(exc_info.value)


def test_overlap_must_be_less_than_chunk_size() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _create(
            {
                "chunking": {
                    "strategy": "custom",
                    "custom": {"mode": "length"},
                    "length": {"chunkSize": 256, "overlap": 256, "maxChunkSize": 512},
                }
            }
        )

    assert INVALID_OPTIONS_CODE in str(exc_info.value)


def test_chunk_size_must_not_exceed_max_chunk_size() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _create(
            {
                "chunking": {
                    "strategy": "custom",
                    "custom": {"mode": "length"},
                    "length": {"chunkSize": 1024, "overlap": 0, "maxChunkSize": 512},
                }
            }
        )

    assert INVALID_OPTIONS_CODE in str(exc_info.value)


def test_non_positive_numeric_rejected() -> None:
    with pytest.raises(ValidationError):
        _create(
            {
                "chunking": {
                    "strategy": "custom",
                    "custom": {"mode": "paragraph"},
                    "paragraph": {"useModel": False, "maxDepth": 0},
                }
            }
        )


def test_custom_requires_mode() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _create({"chunking": {"strategy": "custom"}})

    assert INVALID_OPTIONS_CODE in str(exc_info.value)


def test_custom_length_requires_length_config() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _create({"chunking": {"strategy": "custom", "custom": {"mode": "length"}}})

    assert INVALID_OPTIONS_CODE in str(exc_info.value)


def test_separator_must_be_non_empty() -> None:
    with pytest.raises(ValidationError):
        _create(
            {
                "chunking": {
                    "strategy": "custom",
                    "custom": {"mode": "separator"},
                    "separator": {"separators": []},
                }
            }
        )


def test_legacy_chunk_strategy_still_supported() -> None:
    req = _create({"chunkStrategy": "page"})

    assert req.chunking is None
    assert req.chunk_strategy == "page"


def test_chunking_takes_precedence_over_legacy_field() -> None:
    req = _create({"chunkStrategy": "page", "chunking": {"strategy": "default"}})

    assert req.chunk_strategy == "default"


def test_retry_request_applies_chunking() -> None:
    req = RetryImportJobRequest.model_validate(
        {
            "chunking": {
                "strategy": "custom",
                "custom": {"mode": "length"},
                "length": {"chunkSize": 256, "overlap": 16, "maxChunkSize": 512},
                "metadata": {"includeFileName": False, "includeHeadings": True},
            }
        }
    )

    assert req.options is not None
    assert req.options.chunk_strategy == "custom"
    assert req.options.chunk_size == 256
    assert req.options.chunk_overlap == 16
    assert req.options.include_file_name is False
    assert req.options.include_headings is True
