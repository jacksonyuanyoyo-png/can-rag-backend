from __future__ import annotations

import hashlib
import math
import re


class HashEmbeddingService:
    """无外部模型依赖的确定性向量占位实现。

    生产环境应替换为 OpenAI embeddings、FastGPT embedding 或公司内部模型。
    """

    def __init__(self, dimensions: int) -> None:
        if dimensions <= 0:
            raise ValueError("dimensions 必须大于 0")
        self._dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self._dimensions
        for token in self._tokens(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self._dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign

        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0:
            return vector
        return [v / norm for v in vector]

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(text) for text in texts]

    @staticmethod
    def _tokens(text: str) -> list[str]:
        return re.findall(r"[\w\u4e00-\u9fff]+", text.lower())
