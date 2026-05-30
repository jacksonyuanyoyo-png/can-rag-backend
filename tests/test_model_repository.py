from __future__ import annotations

from uuid import uuid4

import pytest

from app.domain.model_catalog import LEGACY_PLACEHOLDER_MODEL_IDS, openai_chat_model_catalog
from app.repositories.model_repository import (
    ModelCodeDuplicatedError,
    ModelRepository,
)


@pytest.fixture
def model_repo(database_url: str, db_connection) -> ModelRepository:
    repo = ModelRepository(database_url, connection=db_connection)
    repo.ensure_schema()
    return repo


def test_list_active_and_get(model_repo: ModelRepository) -> None:
    suffix = uuid4().hex[:8]
    model_id = f"mdl_{suffix}"
    code = f"gpt-test-{suffix}"

    created = model_repo.create(
        model_id=model_id,
        code=code,
        display_name="GPT Test",
        icon="/models/openai.svg",
        provider="openai",
    )
    assert created.id == model_id
    assert created.code == code

    fetched = model_repo.get_by_id(model_id)
    assert fetched is not None
    assert fetched.display_name == "GPT Test"

    active = model_repo.list_active()
    assert any(item.id == model_id for item in active)


def test_sync_openai_catalog_upserts_and_deactivates_legacy(model_repo: ModelRepository) -> None:
    legacy_id = next(iter(LEGACY_PLACEHOLDER_MODEL_IDS))
    model_repo.create(
        model_id=legacy_id,
        code=legacy_id,
        display_name="Legacy",
        provider="other",
    )

    model_repo.sync_openai_catalog(openai_chat_model_catalog())

    active_ids = {item.id for item in model_repo.list_active()}
    assert "gpt-4o-mini" in active_ids
    assert legacy_id not in active_ids

    legacy = model_repo.get_by_id(legacy_id)
    assert legacy is not None
    assert legacy.status == "inactive"


def test_duplicate_code_raises(model_repo: ModelRepository) -> None:
    suffix = uuid4().hex[:8]
    code = f"dup-code-{suffix}"
    model_repo.create(
        model_id=f"mdl_{suffix}_1",
        code=code,
        display_name="First",
    )

    with pytest.raises(ModelCodeDuplicatedError):
        model_repo.create(
            model_id=f"mdl_{suffix}_2",
            code=code,
            display_name="Second",
        )
