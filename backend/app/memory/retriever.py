"""FTS5 BM25 与 sqlite-vec 的混合记忆检索。"""

from __future__ import annotations

from collections.abc import Sequence

from .embedder import MemoryEmbedder
from .models import MemorySearchResult
from .store import SQLiteMemoryStore


class HybridMemoryRetriever:
    """使用 RRF 合并词法榜单和语义榜单。"""

    def __init__(
        self,
        store: SQLiteMemoryStore,
        embedder: MemoryEmbedder,
        *,
        rrf_constant: int = 60,
    ) -> None:
        if store.embedding_dimensions != embedder.dimensions:
            raise ValueError("store and embedder dimensions must match")
        self._store = store
        self._embedder = embedder
        self._rrf_constant = rrf_constant

    async def retrieve(
        self,
        query: str,
        *,
        namespaces: Sequence[str],
        limit: int = 5,
        candidate_limit: int = 20,
    ) -> tuple[MemorySearchResult, ...]:
        if not query.strip() or not namespaces:
            return ()
        query_embedding = (await self._embedder.embed((query,)))[0]
        lexical = await self._store.lexical_search(
            query,
            namespaces=namespaces,
            limit=candidate_limit,
        )
        vector = await self._store.vector_search(
            query_embedding,
            namespaces=namespaces,
            limit=candidate_limit,
        )
        combined: dict[str, dict[str, object]] = {}
        for rank, (memory, score) in enumerate(lexical, 1):
            entry = combined.setdefault(memory.id, {"memory": memory, "score": 0.0})
            entry["score"] = float(entry["score"]) + 1 / (self._rrf_constant + rank)
            entry["lexical_rank"] = rank
            entry["lexical_score"] = score
        for rank, (memory, distance) in enumerate(vector, 1):
            entry = combined.setdefault(memory.id, {"memory": memory, "score": 0.0})
            entry["score"] = float(entry["score"]) + 1 / (self._rrf_constant + rank)
            entry["vector_rank"] = rank
            entry["vector_distance"] = distance
        ranked = sorted(
            combined.values(),
            key=lambda entry: (
                float(entry["score"])
                + 0.005 * entry["memory"].importance
            ),
            reverse=True,
        )[:limit]
        results = tuple(
            MemorySearchResult(
                memory=entry["memory"],
                score=float(entry["score"]),
                lexical_rank=entry.get("lexical_rank"),
                vector_rank=entry.get("vector_rank"),
                lexical_score=entry.get("lexical_score"),
                vector_distance=entry.get("vector_distance"),
            )
            for entry in ranked
        )
        await self._store.record_access([result.memory.id for result in results])
        return results


__all__ = ["HybridMemoryRetriever"]
