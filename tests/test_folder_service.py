from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.errors import BusinessError, ErrorCode
from app.repositories.folder_repository import FolderRepository
from app.services.folder_service import FolderService


@pytest.fixture
def folder_service(database_url: str, db_connection) -> FolderService:
    repo = FolderRepository(database_url, connection=db_connection)
    repo.ensure_schema()
    return FolderService(repo)


def test_service_maps_duplicate_and_not_found(folder_service: FolderService) -> None:
    owner_id = f"user_{uuid4().hex[:8]}"

    created = folder_service.create_folder(name="Team Docs", owner_user_id=owner_id)
    assert created.name == "Team Docs"

    folders = folder_service.list_folders(owner_user_id=owner_id)
    assert len(folders) == 1

    with pytest.raises(BusinessError) as duplicate_exc:
        folder_service.create_folder(name="Team Docs", owner_user_id=owner_id)
    assert duplicate_exc.value.code == ErrorCode.FOLDER_NAME_DUPLICATED

    updated = folder_service.update_folder(
        folder_id=created.id,
        name="Renamed",
        owner_user_id=owner_id,
    )
    assert updated.name == "Renamed"

    folder_service.delete_folder(folder_id=created.id, owner_user_id=owner_id)

    with pytest.raises(BusinessError) as missing_exc:
        folder_service.update_folder(
            folder_id=created.id,
            name="Ghost",
            owner_user_id=owner_id,
        )
    assert missing_exc.value.code == ErrorCode.FOLDER_NOT_FOUND

    with pytest.raises(BusinessError) as delete_missing_exc:
        folder_service.delete_folder(folder_id=created.id, owner_user_id=owner_id)
    assert delete_missing_exc.value.code == ErrorCode.FOLDER_NOT_FOUND


def test_empty_name_validation(folder_service: FolderService) -> None:
    with pytest.raises(BusinessError) as exc:
        folder_service.create_folder(name="   ", owner_user_id="user_test")
    assert exc.value.code == ErrorCode.VALIDATION_ERROR
