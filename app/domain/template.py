from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


class TemplateScope(StrEnum):
    PERSONAL = "personal"
    TEAM = "team"
    SYSTEM = "system"


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_template_id() -> str:
    return f"tmpl_{uuid4().hex[:12]}"


def build_snippet(content: str, *, max_length: int = 100) -> str:
    normalized = content.strip()
    if len(normalized) <= max_length:
        return normalized
    return normalized[:max_length] + "..."


@dataclass(slots=True)
class Template:
    id: str
    name: str
    content: str
    owner_user_id: str
    snippet: str = ""
    scope: TemplateScope = TemplateScope.PERSONAL
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def to_api(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "content": self.content,
            "snippet": self.snippet,
            "createdAt": self.created_at.isoformat(),
            "updatedAt": self.updated_at.isoformat(),
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Template:
        return cls(
            id=str(row["id"]),
            name=str(row["name"]),
            content=str(row["content"]),
            owner_user_id=str(row["owner_user_id"]),
            snippet=str(row.get("snippet") or ""),
            scope=TemplateScope(str(row.get("scope") or TemplateScope.PERSONAL)),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
