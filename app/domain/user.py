from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.auth.dev_users import DevUser


@dataclass(frozen=True, slots=True)
class User:
    id: str
    email: str
    display_name: str
    permissions: list[str]
    team_id: str
    status: str = "active"

    @classmethod
    def from_row(
        cls,
        row: dict[str, Any],
        *,
        permissions: list[str],
        team_id: str,
    ) -> User:
        return cls(
            id=str(row["id"]),
            email=str(row["email"]),
            display_name=str(row["display_name"]),
            permissions=list(permissions),
            team_id=team_id,
            status=str(row.get("status") or "active"),
        )

    @classmethod
    def from_dev_user(cls, dev_user: DevUser) -> User:
        return cls(
            id=dev_user.id,
            email=dev_user.email,
            display_name=dev_user.display_name,
            permissions=list(dev_user.permissions),
            team_id=dev_user.team_id,
        )
