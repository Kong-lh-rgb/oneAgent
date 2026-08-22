"""长期记忆的统一 Runtime 门面。

Runtime 不直接操作文件路径，统一通过 ``MemoryManager``：

- 加载 Core Memory、Memory Index 与 Memory Policy；
- 为在线 Recall、显式 Core 写入与 Post-Run Reflection 提供统一 API；
- 维护运行时元数据（access_count、last_accessed_at、updated_at）；
- 执行容量管理与 INDEX 重建；
- 不做任何 query-driven 自动检索或 Top-K 注入。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows 暂时只保留进程内锁
    fcntl = None  # type: ignore[assignment]

from app.models.types import Message, MessageRole

from .core import DEFAULT_MAX_CORE_TOKENS, CoreMemoryEntry, CoreMemoryManager
from .index import MemoryIndex
from .maintenance import MemoryMaintenance
from .models import MemoryRecord
from .prompts import (
    CORE_MEMORY_HEADER,
    MEMORY_POLICY_PROMPT,
)
from .store import DEFAULT_MEMORY_DIR, MemoryStore

CORE_MEMORY_MESSAGE_NAME = "vesta_core_memory"
MEMORY_INDEX_MESSAGE_NAME = "vesta_memory_index"
MEMORY_POLICY_MESSAGE_NAME = "vesta_memory_policy"


class MemoryManager:
    """Sparse, Model-Directed Long-Term Memory 的 Runtime 门面。"""

    def __init__(
        self,
        memory_dir: str | Path = DEFAULT_MEMORY_DIR,
        *,
        max_active: int = 25,
        max_core_tokens: int = DEFAULT_MAX_CORE_TOKENS,
    ) -> None:
        self.memory_dir = Path(memory_dir).expanduser().resolve()
        self.max_active = max_active
        self.store = MemoryStore(self.memory_dir, max_active=max_active)
        self.core = CoreMemoryManager(
            self.memory_dir,
            max_tokens=max_core_tokens,
        )
        self.index = MemoryIndex(self.memory_dir)
        self.maintenance = MemoryMaintenance(max_active=max_active)
        self._lock = asyncio.Lock()
        self._lock_path = self.memory_dir / ".memory.lock"

    async def initialize(self) -> None:
        """创建 memory 目录结构。"""

        async with self._mutation_guard():
            await self.store.initialize()
            await self.core.initialize()
            # INDEX 是 active 文件的投影；启动时重建可修复中断或人工编辑造成的陈旧。
            await self._rebuild_index()

    # ------------------------------------------------------------------
    # Runtime 注入
    # ------------------------------------------------------------------

    async def context_messages(self) -> tuple[Message, ...]:
        """返回应注入请求上下文的消息（Core + Index + Policy）。"""

        async with self._lock:
            messages: list[Message] = []
            core_text = await self.core.load()
            if core_text.strip():
                core_content = core_text.strip()
                if not core_content.startswith(CORE_MEMORY_HEADER):
                    core_content = f"{CORE_MEMORY_HEADER}\n\n{core_content}"
                messages.append(
                    Message(
                        role=MessageRole.SYSTEM,
                        name=CORE_MEMORY_MESSAGE_NAME,
                        content=core_content,
                    )
                )
            index_text = await self.index.load()
            if index_text is not None:
                messages.append(
                    Message(
                        role=MessageRole.SYSTEM,
                        name=MEMORY_INDEX_MESSAGE_NAME,
                        content=index_text,
                    )
                )
            messages.append(
                Message(
                    role=MessageRole.SYSTEM,
                    name=MEMORY_POLICY_MESSAGE_NAME,
                    content=MEMORY_POLICY_PROMPT,
                )
            )
            return tuple(messages)

    async def reflection_context(self) -> tuple[str, str]:
        """返回 Post-Run Reflection 使用的 Core 正文与当前 Index。"""

        async with self._lock:
            core_text = await self.core.load()
            index_text = await self.index.load()
            return core_text, index_text or ""

    # ------------------------------------------------------------------
    # 语义 Memory API（由模型工具调用）
    # ------------------------------------------------------------------

    async def read(self, memory_id: str) -> MemoryRecord | None:
        """读取完整记忆；自动更新 access_count / last_accessed_at。"""

        async with self._mutation_guard():
            return await self.store.read(memory_id)

    async def list(self) -> tuple[MemoryRecord, ...]:
        """列出当前 active 记忆（id / title / summary）。"""

        async with self._lock:
            return await self.store.list_active()

    async def list_archived(self) -> tuple[MemoryRecord, ...]:
        """列出已归档记忆，仅供管理与观察界面读取。"""

        async with self._lock:
            return await self.store.list_archived()

    async def create(
        self,
        *,
        title: str,
        summary: str,
        content: str,
    ) -> MemoryRecord:
        """在硬容量范围内创建普通长期记忆并重建 INDEX。"""

        async with self._mutation_guard():
            if await self.store.count_active() >= self.max_active:
                raise ValueError("active memory capacity is full")
            record = await self.store.create(
                title=title,
                summary=summary,
                content=content,
            )
            await self._rebuild_index()
            return record

    async def create_if_capacity(
        self,
        *,
        title: str,
        summary: str,
        content: str,
    ) -> MemoryRecord | None:
        """在同一临界区检查容量并创建，避免并发突破 active 上限。"""

        async with self._mutation_guard():
            if await self.store.count_active() >= self.max_active:
                return None
            record = await self.store.create(
                title=title,
                summary=summary,
                content=content,
            )
            await self._rebuild_index()
            return record

    @staticmethod
    def validate_create(
        *,
        title: str,
        summary: str,
        content: str,
    ) -> None:
        """在容量维护前验证 CREATE 内容，避免先归档后才发现输入非法。"""

        now = datetime.now(UTC)
        MemoryRecord(
            id="M000",
            title=title,
            summary=summary,
            content=content,
            created_at=now,
            updated_at=now,
            last_accessed_at=now,
        )

    async def update(
        self,
        memory_id: str,
        *,
        title: str | None = None,
        summary: str | None = None,
        content: str,
        reason: str,
    ) -> MemoryRecord:
        """管理性更新已有记忆；未提供 Recall Cue 时保持原值。"""

        async with self._mutation_guard():
            record = await self.store.update(
                memory_id,
                title=title,
                summary=summary,
                content=content,
                reason=reason,
            )
            await self._rebuild_index()
            return record

    async def update_if_revision(
        self,
        memory_id: str,
        *,
        expected_revision: int,
        title: str,
        summary: str,
        content: str,
        reason: str,
    ) -> MemoryRecord:
        """仅在模型读取的 revision 仍为最新时替换完整记忆。"""

        async with self._mutation_guard():
            record = await self.store.update(
                memory_id,
                title=title,
                summary=summary,
                content=content,
                reason=reason,
                expected_revision=expected_revision,
            )
            await self._rebuild_index()
            return record

    async def archive(self, memory_id: str, *, reason: str) -> MemoryRecord:
        """归档记忆并重建 INDEX。"""

        async with self._mutation_guard():
            record = await self.store.archive(memory_id, reason=reason)
            await self._rebuild_index()
            return record

    async def archive_if_unchanged(
        self,
        memory_id: str,
        *,
        expected_record: MemoryRecord,
        reason: str,
    ) -> MemoryRecord:
        """候选快照仍为最新时归档，拒绝维护模型基于陈旧内容执行。"""

        async with self._mutation_guard():
            current = await self.store.load(memory_id)
            if current is None:
                raise KeyError(f"memory '{memory_id}' not found")
            if current != expected_record:
                raise ValueError(
                    f"memory '{memory_id}' changed since maintenance snapshot"
                )
            record = await self.store.archive(memory_id, reason=reason)
            await self._rebuild_index()
            return record

    async def upsert_core(
        self,
        *,
        key: str,
        value: str,
        reason: str,
        source_statement: str,
    ) -> tuple[CoreMemoryEntry, bool]:
        """按 key 更新 Core；模型不能覆盖整份 CORE.md。"""

        async with self._mutation_guard():
            return await self.core.upsert(
                key=key,
                value=value,
                reason=reason,
                source_statement=source_statement,
            )

    async def remove_core(self, key: str) -> CoreMemoryEntry:
        """移除单个 Core 条目；调用方负责验证当前用户明确证据。"""

        async with self._mutation_guard():
            return await self.core.remove(key)

    # ------------------------------------------------------------------
    # 容量管理
    # ------------------------------------------------------------------

    async def maintenance_required(self) -> bool:
        """active 数量是否超过上限。"""

        async with self._lock:
            return self.maintenance.exceeds_capacity(
                await self.store.count_active()
            )

    async def active_count(self) -> int:
        """返回当前 active Memory 数量。"""

        async with self._lock:
            return await self.store.count_active()

    async def has_capacity(self, *, required_slots: int = 1) -> bool:
        """判断是否能在不超过硬上限的情况下容纳指定新记录数。"""

        if required_slots < 0:
            raise ValueError("required_slots cannot be negative")
        async with self._lock:
            active_count = await self.store.count_active()
            return active_count + required_slots <= self.max_active

    async def retention_candidates(
        self,
        *,
        limit: int = 5,
    ) -> tuple[MemoryRecord, ...]:
        """返回最可能值得维护的候选（最终决策交给模型）。"""

        async with self._lock:
            active = await self.store.list_active()
            return self.maintenance.select_candidates(active, limit=limit)

    async def _rebuild_index(self) -> None:
        await self.index.rebuild(await self.store.list_active())

    @asynccontextmanager
    async def _mutation_guard(self) -> AsyncIterator[None]:
        """串行化同一实例，并在 POSIX 上协调同目录的进程与 Manager。"""

        async with self._lock:
            await asyncio.to_thread(self.memory_dir.mkdir, parents=True, exist_ok=True)
            handle = await asyncio.to_thread(self._lock_path.open, "a+b")
            try:
                await self._acquire_file_lock(handle)
                yield
            finally:
                await self._release_file_lock(handle)

    @staticmethod
    async def _acquire_file_lock(handle: BinaryIO) -> None:
        if fcntl is not None:
            await asyncio.to_thread(fcntl.flock, handle.fileno(), fcntl.LOCK_EX)

    @staticmethod
    async def _release_file_lock(handle: BinaryIO) -> None:
        try:
            if fcntl is not None:
                await asyncio.to_thread(fcntl.flock, handle.fileno(), fcntl.LOCK_UN)
        finally:
            await asyncio.to_thread(handle.close)


__all__ = [
    "CORE_MEMORY_MESSAGE_NAME",
    "MEMORY_INDEX_MESSAGE_NAME",
    "MEMORY_POLICY_MESSAGE_NAME",
    "MemoryManager",
]
