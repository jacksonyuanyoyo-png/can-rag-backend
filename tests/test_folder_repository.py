from __future__ import annotations

from uuid import uuid4

import pytest

from app.repositories.folder_repository import (
    FolderNameDuplicatedError,
    FolderNotFoundError,
    FolderRepository,
)


@pytest.fixture
def folder_repo(database_url: str, db_connection) -> FolderRepository:
    repo = FolderRepository(database_url, connection=db_connection)
    repo.ensure_schema()
    return repo


def test_list_create_update_delete(folder_repo: FolderRepository) -> None:
    owner_id = f"user_{uuid4().hex[:8]}"

    assert folder_repo.list_by_owner(owner_id) == []

    created = folder_repo.create(name="Work Projects", owner_user_id=owner_id)
    assert created.name == "Work Projects"
    assert created.owner_user_id == owner_id
    assert created.team_id is None

    listed = folder_repo.list_by_owner(owner_id)
    assert len(listed) == 1
    assert listed[0].id == created.id

    updated = folder_repo.update(
        created.id,
        owner_user_id=owner_id,
        name="Personal",
    )
    assert updated.name == "Personal"
    assert updated.updated_at >= created.updated_at

    folder_repo.delete(created.id, owner_user_id=owner_id)
    assert folder_repo.get(created.id) is None


def test_duplicate_name_raises(folder_repo: FolderRepository) -> None:
    owner_id = f"user_{uuid4().hex[:8]}"
    folder_repo.create(name="Duplicates", owner_user_id=owner_id)

    with pytest.raises(FolderNameDuplicatedError):
        folder_repo.create(name="Duplicates", owner_user_id=owner_id)


def test_update_duplicate_name_raises(folder_repo: FolderRepository) -> None:
    owner_id = f"user_{uuid4().hex[:8]}"
    first = folder_repo.create(name="Alpha", owner_user_id=owner_id)
    folder_repo.create(name="Beta", owner_user_id=owner_id)

    with pytest.raises(FolderNameDuplicatedError):
        folder_repo.update(first.id, owner_user_id=owner_id, name="Beta")


def test_not_found_on_update_and_delete(folder_repo: FolderRepository) -> None:
    owner_id = f"user_{uuid4().hex[:8]}"

    with pytest.raises(FolderNotFoundError):
        folder_repo.update("fld_missing", owner_user_id=owner_id, name="X")

    with pytest.raises(FolderNotFoundError):
        folder_repo.delete("fld_missing", owner_user_id=owner_id)
