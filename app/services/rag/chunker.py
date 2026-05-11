from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class TextChunk:
    chunk_id: str
    text: str
    start: int
    end: int


class TextChunker:
    def __init__(self, chunk_size: int, overlap: int) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size 必须大于 0")
        if overlap < 0 or overlap >= chunk_size:
            raise ValueError("overlap 必须满足 0 <= overlap < chunk_size")
        self._chunk_size = chunk_size
        self._overlap = overlap

    def split(self, text: str) -> list[TextChunk]:
        stripped = text.strip()
        if not stripped:
            return []

        chunks: list[TextChunk] = []
        start = 0
        index = 0
        step = self._chunk_size - self._overlap
        while start < len(stripped):
            end = min(start + self._chunk_size, len(stripped))
            chunk_text = stripped[start:end].strip()
            if chunk_text:
                chunks.append(
                    TextChunk(
                        chunk_id=f"chunk-{index:06d}",
                        text=chunk_text,
                        start=start,
                        end=end,
                    )
                )
                index += 1
            if end == len(stripped):
                break
            start += step
        return chunks
