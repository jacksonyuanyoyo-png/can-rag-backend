from __future__ import annotations

import pytest

from app.domain.import_job import ImportJobStage
from app.services.import_job_progress import (
    parse_page_progress,
    progress_for_stage,
)


def test_progress_for_stage_parse_single_file() -> None:
    assert progress_for_stage(
        ImportJobStage.PARSE,
        file_index=0,
        file_count=1,
        fraction=0.0,
    ) == 10
    assert progress_for_stage(
        ImportJobStage.PARSE,
        file_index=0,
        file_count=1,
        fraction=1.0,
    ) == 35


def test_parse_page_progress_maps_to_parse_range() -> None:
    fraction = parse_page_progress(2, 10)
    progress = progress_for_stage(
        ImportJobStage.PARSE,
        file_index=0,
        file_count=1,
        fraction=fraction,
    )
    assert progress == 10 + int(25 * 0.2)


def test_progress_for_stage_multi_file() -> None:
    mid = progress_for_stage(
        ImportJobStage.EMBED,
        file_index=1,
        file_count=2,
        fraction=0.5,
    )
    assert 55 <= mid <= 80


def test_progress_for_stage_clamps_fraction() -> None:
    low = progress_for_stage(
        ImportJobStage.CHUNK,
        file_index=0,
        file_count=1,
        fraction=-1.0,
    )
    high = progress_for_stage(
        ImportJobStage.CHUNK,
        file_index=0,
        file_count=1,
        fraction=2.0,
    )
    assert low == 35
    assert high == 55


def test_progress_for_stage_requires_positive_file_count() -> None:
    with pytest.raises(ValueError, match="file_count"):
        progress_for_stage(
            ImportJobStage.PARSE,
            file_index=0,
            file_count=0,
            fraction=0.0,
        )
