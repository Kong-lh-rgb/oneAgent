"""长期记忆容量管理与 Retention 候选选择。

当 active 记忆数超过上限（默认 25）时触发 Memory Maintenance。算法只负责
找出最可能值得维护的 3~5 个候选（按使用时间与次数），最终的
KEEP / MERGE / ARCHIVE 判断交给模型。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import UTC, datetime

from .models import MemoryRecord


class MemoryMaintenance:
    """容量检查与 Retention 候选选择。"""

    def __init__(self, *, max_active: int = 25) -> None:
        if max_active <= 0:
            raise ValueError("max_active must be greater than zero")
        self.max_active = max_active

    def exceeds_capacity(self, active_count: int) -> bool:
        """active 数量是否超过上限（触发维护）。"""

        return active_count > self.max_active

    def select_candidates(
        self,
        memories: Sequence[MemoryRecord],
        *,
        limit: int = 5,
    ) -> tuple[MemoryRecord, ...]:
        """按 retention score 选出最可能值得维护的候选。

        score 越低越可能被维护。考虑三个信号：最近使用时间、使用次数、
        最近更新时间。公式只负责排序，不做最终删除决策。
        """

        if limit <= 0:
            return ()
        now = datetime.now(UTC)
        scored = sorted(
            memories,
            key=lambda record: _retention_score(record, now),
        )
        return tuple(scored[:limit])


def _retention_score(record: MemoryRecord, now: datetime) -> float:
    hours_since_accessed = max(
        0.0, (now - record.last_accessed_at).total_seconds() / 3600
    )
    hours_since_updated = max(
        0.0, (now - record.updated_at).total_seconds() / 3600
    )
    # 访问越少、越久未访问/更新的记忆分越低，越值得被检查。
    access_score = math.log1p(record.access_count)
    return -hours_since_accessed + access_score + -hours_since_updated


__all__ = ["MemoryMaintenance"]
