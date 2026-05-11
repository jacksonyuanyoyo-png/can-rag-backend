from __future__ import annotations

import json
from pathlib import Path

from app.domain.knowledge_base import KnowledgeBaseMetadata, utc_now_iso


class KnowledgeBaseRepository:
    """JSON 文件仓储，先解决重启丢元数据的问题，后续可替换为 DB。"""

    def __init__(self, metadata_path: Path) -> None:
        self._metadata_path = metadata_path
        self._metadata_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._metadata_path.exists():
            self._write_all({})

    def list(self) -> list[KnowledgeBaseMetadata]:
        return sorted(self._read_all().values(), key=lambda kb: kb.name)

    def get(self, name: str) -> KnowledgeBaseMetadata | None:
        return self._read_all().get(name)

    def require(self, name: str) -> KnowledgeBaseMetadata:
        kb = self.get(name)
        if kb is None:
            raise ValueError(f"知识库不存在: {name}")
        return kb

    def save(self, metadata: KnowledgeBaseMetadata) -> KnowledgeBaseMetadata:
        all_kbs = self._read_all()
        metadata.updated_at = utc_now_iso()
        all_kbs[metadata.name] = metadata
        self._write_all(all_kbs)
        return metadata

    def delete(self, name: str) -> None:
        all_kbs = self._read_all()
        if name not in all_kbs:
            raise ValueError(f"知识库不存在: {name}")
        del all_kbs[name]
        self._write_all(all_kbs)

    def _read_all(self) -> dict[str, KnowledgeBaseMetadata]:
        try:
            raw = json.loads(self._metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"知识库元数据文件损坏: {self._metadata_path}") from exc
        items = raw.get("knowledge_bases", {})
        return {
            name: KnowledgeBaseMetadata.from_dict(metadata)
            for name, metadata in dict(items).items()
        }

    def _write_all(self, items: dict[str, KnowledgeBaseMetadata]) -> None:
        payload = {
            "knowledge_bases": {
                name: metadata.to_dict()
                for name, metadata in sorted(items.items())
            }
        }
        tmp = self._metadata_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._metadata_path)
