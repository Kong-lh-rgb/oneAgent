"""记忆规范化、去重、冲突处理与写入预算。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from .embedder import MemoryEmbedder
from .models import MemoryDraft, MemoryItem, MemoryStatus, MemoryType
from .store import SQLiteMemoryStore

_PUNCTUATION_RE = re.compile(r"[\s\u3000，。！？；：、,.!?;:'\"`]+")


class MemoryWriteAction(StrEnum):
    CREATED = "created"
    DUPLICATE = "duplicate"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


@dataclass(frozen=True)
class MemoryWriteResult:
    action: MemoryWriteAction
    memory: MemoryItem | None = None
    previous: MemoryItem | None = None
    reason: str | None = None


@dataclass(frozen=True)
class MemoryWriteBudget:
    per_run: int = 3
    per_session: int = 5
    per_day: int = 20


class MemoryWriter:
    """Store 上方唯一的策略写入口。"""

    def __init__(
        self,
        store: SQLiteMemoryStore,
        embedder: MemoryEmbedder,
        *,
        budget: MemoryWriteBudget | None = None,
    ) -> None:
        if store.embedding_dimensions != embedder.dimensions:
            raise ValueError("store and embedder dimensions must match")
        self._store = store
        self._embedder = embedder
        self._budget = budget or MemoryWriteBudget()

    async def write(self, draft: MemoryDraft) -> MemoryWriteResult:
        """规范化、指纹去重、检查预算，再原子写入或替代。"""

        normalized = normalize_memory_content(draft.content)
        fingerprint = memory_fingerprint(
            draft.namespace,
            draft.memory_type,
            normalized,
        )
        duplicate = await self._store.find_by_fingerprint(fingerprint)
        if duplicate is not None:
            return MemoryWriteResult(
                MemoryWriteAction.DUPLICATE,
                memory=duplicate,
                reason="fingerprint matched existing memory",
            )
        budget_error = await self._budget_error(draft)
        if budget_error:
            return MemoryWriteResult(MemoryWriteAction.REJECTED, reason=budget_error)
        embedding = (await self._embedder.embed((normalized,)))[0]
        conflict = None
        if (
            draft.memory_type is MemoryType.FACT
            and draft.status is MemoryStatus.ACTIVE
            and draft.key
        ):
            conflict = await self._store.find_active_fact(
                namespace=draft.namespace,
                key=draft.key,
            )
        if conflict is not None:
            retired, replacement = await self._store.replace(
                conflict.id,
                draft,
                normalized_content=normalized,
                fingerprint=fingerprint,
                embedding=embedding,
                expected_revision=conflict.revision,
            )
            return MemoryWriteResult(
                MemoryWriteAction.SUPERSEDED,
                memory=replacement,
                previous=retired,
            )
        memory = await self._store.create(
            draft,
            normalized_content=normalized,
            fingerprint=fingerprint,
            embedding=embedding,
        )
        return MemoryWriteResult(MemoryWriteAction.CREATED, memory=memory)

    async def promote(
        self,
        memory_id: str,
        *,
        expected_revision: int | None = None,
    ) -> MemoryItem:
        """用户确认或上层规则确认后，将候选晋升为有效记忆。"""

        return await self._store.change_status(
            memory_id,
            MemoryStatus.ACTIVE,
            expected_revision=expected_revision,
            confirmed=True,
        )

    async def promote_after_use(
        self,
        memory_id: str,
        *,
        expected_revision: int | None = None,
    ) -> MemoryItem:
        """候选在真实任务中被采用后晋升，不伪装成用户确认。"""

        await self._store.record_access((memory_id,), used=True)
        return await self._store.change_status(
            memory_id,
            MemoryStatus.ACTIVE,
            expected_revision=expected_revision,
            confirmed=False,
        )

    async def _budget_error(self, draft: MemoryDraft) -> str | None:
        now = datetime.now(UTC)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if await self._store.count_writes(since=day_start) >= self._budget.per_day:
            return "daily memory write budget exceeded"
        if draft.source.session_id:
            count = await self._store.count_writes(
                since=datetime.min.replace(tzinfo=UTC),
                source_session_id=draft.source.session_id,
            )
            if count >= self._budget.per_session:
                return "session memory write budget exceeded"
        if draft.source.run_id:
            count = await self._store.count_writes(
                since=datetime.min.replace(tzinfo=UTC),
                source_run_id=draft.source.run_id,
            )
            if count >= self._budget.per_run:
                return "run memory write budget exceeded"
        return None


def normalize_memory_content(content: str) -> str:
    """生成不受空白和常见标点影响的指纹文本。"""

    return _PUNCTUATION_RE.sub("", content).lower()


def memory_fingerprint(
    namespace: str,
    memory_type: MemoryType,
    normalized_content: str,
) -> str:
    payload = f"{namespace.strip()}\x1f{memory_type.value}\x1f{normalized_content}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "MemoryWriteAction",
    "MemoryWriteBudget",
    "MemoryWriteResult",
    "MemoryWriter",
    "memory_fingerprint",
    "normalize_memory_content",
]
