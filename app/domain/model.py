from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class Model:
    id: str
    code: str
    display_name: str
    icon: str | None = None
    provider: str | None = None
    status: str = "active"
    visibility: str = "system"

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Model:
        return cls(
            id=str(row["id"]),
            code=str(row["code"]),
            display_name=str(row["display_name"]),
            icon=str(row["icon"]) if row.get("icon") is not None else None,
            provider=str(row["provider"]) if row.get("provider") is not None else None,
            status=str(row.get("status") or "active"),
            visibility=str(row.get("visibility") or "system"),
        )
