"""记忆向量生成的模型无关接口。"""

from __future__ import annotations

import hashlib
import math
from abc import ABC, abstractmethod
from collections.abc import Sequence

from openai import AsyncOpenAI


class MemoryEmbedder(ABC):
    """把文本转换成固定维度向量。"""

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """返回向量维度。"""

    @abstractmethod
    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        """按输入顺序批量生成向量。"""


class HashMemoryEmbedder(MemoryEmbedder):
    """确定性离线向量器，仅用于测试和无模型演示。"""

    def __init__(self, dimensions: int = 64) -> None:
        if dimensions < 8:
            raise ValueError("hash embedding dimensions must be at least 8")
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return tuple(self._embed_one(text) for text in texts)

    def _embed_one(self, text: str) -> tuple[float, ...]:
        values = [0.0] * self._dimensions
        normalized = " ".join(text.lower().split())
        fragments = _fragments(normalized)
        for fragment in fragments:
            digest = hashlib.sha256(fragment.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self._dimensions
            values[index] += 1.0 if digest[4] & 1 else -1.0
        magnitude = math.sqrt(sum(value * value for value in values)) or 1.0
        return tuple(value / magnitude for value in values)


class OpenAICompatibleMemoryEmbedder(MemoryEmbedder):
    """调用 OpenAI 兼容 Embeddings API 的生产向量器。"""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        dimensions: int,
        base_url: str | None = None,
    ) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        if not texts:
            return ()
        response = await self._client.embeddings.create(
            model=self._model,
            input=list(texts),
            dimensions=self._dimensions,
            encoding_format="float",
        )
        ordered = sorted(response.data, key=lambda item: item.index)
        vectors = tuple(
            tuple(float(value) for value in item.embedding) for item in ordered
        )
        if len(vectors) != len(texts):
            raise RuntimeError("embedding provider returned an unexpected item count")
        if any(len(vector) != self._dimensions for vector in vectors):
            raise RuntimeError("embedding provider returned unexpected dimensions")
        return vectors


def _fragments(text: str) -> tuple[str, ...]:
    compact = text.replace(" ", "")
    if len(compact) < 2:
        return (compact or "empty",)
    return tuple(compact[index : index + 2] for index in range(len(compact) - 1))


__all__ = [
    "HashMemoryEmbedder",
    "MemoryEmbedder",
    "OpenAICompatibleMemoryEmbedder",
]
