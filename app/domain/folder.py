from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_folder_id() -> str:
    return f"fld_{uuid4().hex[:12]}"


def format_api_timestamp(value: datetime) -> str:
    iso = value.astimezone(UTC).isoformat()
    return iso.replace("+00:00", "Z")


@dataclass(slots=True)
class Folder:
    id: str
    name: str
    owner_user_id: str
    team_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Folder:
        return cls(
            id=str(row["id"]),
            name=str(row["name"]),
            owner_user_id=str(row["owner_user_id"]),
            team_id=str(row["team_id"]) if row.get("team_id") is not None else None,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def to_api(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "updatedAt": format_api_timestamp(self.updated_at),
        }
