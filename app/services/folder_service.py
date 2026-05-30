from __future__ import annotations

from app.core.errors import BusinessError, ErrorCode
from app.domain.folder import Folder
from app.repositories.folder_repository import (
    FolderNameDuplicatedError,
    FolderNotFoundError,
    FolderRepository,
)


class FolderService:
    """会话文件夹应用服务，将仓储异常映射为统一业务错误码。"""

    def __init__(self, repository: FolderRepository) -> None:
        self._repository = repository

    def list_folders(
        self,
        *,
        owner_user_id: str,
        team_id: str | None = None,
    ) -> list[Folder]:
        return self._repository.list_by_owner(owner_user_id, team_id=team_id)

    def create_folder(
        self,
        *,
        name: str,
        owner_user_id: str,
        team_id: str | None = None,
    ) -> Folder:
        normalized = self._normalize_name(name)
        try:
            return self._repository.create(
                name=normalized,
                owner_user_id=owner_user_id,
                team_id=team_id,
            )
        except FolderNameDuplicatedError as exc:
            raise BusinessError(ErrorCode.FOLDER_NAME_DUPLICATED, str(exc)) from exc

    def update_folder(
        self,
        *,
        folder_id: str,
        name: str,
        owner_user_id: str,
    ) -> Folder:
        normalized = self._normalize_name(name)
        try:
            return self._repository.update(
                folder_id,
                owner_user_id=owner_user_id,
                name=normalized,
            )
        except FolderNotFoundError as exc:
            raise BusinessError(ErrorCode.FOLDER_NOT_FOUND, str(exc)) from exc
        except FolderNameDuplicatedError as exc:
            raise BusinessError(ErrorCode.FOLDER_NAME_DUPLICATED, str(exc)) from exc

    def delete_folder(self, *, folder_id: str, owner_user_id: str) -> None:
        try:
            self._repository.delete(folder_id, owner_user_id=owner_user_id)
        except FolderNotFoundError as exc:
            raise BusinessError(ErrorCode.FOLDER_NOT_FOUND, str(exc)) from exc

    def require_folder(self, *, folder_id: str, owner_user_id: str) -> Folder:
        folder = self._repository.get_for_owner(folder_id, owner_user_id)
        if folder is None:
            raise BusinessError(ErrorCode.FOLDER_NOT_FOUND, "Folder not found")
        return folder

    @staticmethod
    def _normalize_name(name: str) -> str:
        normalized = name.strip()
        if not normalized:
            raise BusinessError(ErrorCode.VALIDATION_ERROR, "Folder name is required")
        return normalized
