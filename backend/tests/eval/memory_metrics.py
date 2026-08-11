"""Memory 检索测评的确定性指标。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.memory import MemorySearchResult, MemoryStatus


@dataclass(frozen=True)
class MemoryRetrievalMetrics:
    recall_at_k: float
    reciprocal_rank: float
    namespace_violations: int
    inactive_violations: int


def evaluate_memory_retrieval(
    results: Sequence[MemorySearchResult],
    *,
    expected_ids: set[str],
    allowed_namespaces: set[str],
) -> MemoryRetrievalMetrics:
    """同时衡量召回率、首个命中排名和隔离违规。"""

    returned_ids = [result.memory.id for result in results]
    matched = expected_ids.intersection(returned_ids)
    recall = len(matched) / len(expected_ids) if expected_ids else 1.0
    first_rank = next(
        (
            rank
            for rank, memory_id in enumerate(returned_ids, 1)
            if memory_id in expected_ids
        ),
        None,
    )
    return MemoryRetrievalMetrics(
        recall_at_k=recall,
        reciprocal_rank=1 / first_rank if first_rank else 0.0,
        namespace_violations=sum(
            result.memory.namespace not in allowed_namespaces for result in results
        ),
        inactive_violations=sum(
            result.memory.status is not MemoryStatus.ACTIVE for result in results
        ),
    )


__all__ = ["MemoryRetrievalMetrics", "evaluate_memory_retrieval"]
