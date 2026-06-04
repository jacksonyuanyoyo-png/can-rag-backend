from __future__ import annotations

import uuid
from pathlib import Path

from app.core.config import get_settings
from app.services.rag.image_normalize import normalize_image_bytes_for_storage

KB_IMAGES_SUBDIR = "kb_images"


class ImageStore:
    def __init__(self, root: Path | str | None = None) -> None:
        if root is None:
            root = get_settings().upload_root_resolved
        self._root = Path(root).resolve()

    def save(self, data: bytes, *, suffix: str) -> str:
        data, normalized = normalize_image_bytes_for_storage(data, suffix=suffix)
        storage_key = f"{KB_IMAGES_SUBDIR}/{uuid.uuid4()}.{normalized}"
        destination = self._root / storage_key
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        return storage_key

    def path_for(self, storage_key: str) -> Path:
        return self._root / storage_key
