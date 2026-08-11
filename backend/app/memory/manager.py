"""长期记忆的统一 Runtime 门面。

Runtime 不直接操作文件路径，统一通过 ``MemoryManager``：

- 加载 Core Memory、Memory Index 与 Memory Policy；
- 暴露 memory.read / list / create / update / archive；
- 维护运行时元数据（access_count、last_accessed_at、updated_at）；
- 执行容量管理与 INDEX 重建；
- 不做任何 query-driven 自动检索或 Top-K 注入。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.models.types import Message, MessageRole

from .core import DEFAULT_MAX_CORE_TOKENS, CoreMemoryManager
from .index import MemoryIndex
from .maintenance import MemoryMaintenance
from .models import MemoryRecord
from .prompts import (
    CORE_MEMORY_HEADER,
    MEMORY_POLICY_PROMPT,
)
from .store import DEFAULT_MEMORY_DIR, MemoryStore

CORE_MEMORY_MESSAGE_NAME = "oneagent_core_memory"
MEMORY_INDEX_MESSAGE_NAME = "oneagent_memory_index"
MEMORY_POLICY_MESSAGE_NAME = "oneagent_memory_policy"


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

    async def initialize(self) -> None:
        """创建 memory 目录结构。"""

        async with self._lock:
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
                messages.append(
                    Message(
                        role=MessageRole.SYSTEM,
                        name=CORE_MEMORY_MESSAGE_NAME,
                        content=f"{CORE_MEMORY_HEADER}\n\n{core_text.strip()}",
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

    # ------------------------------------------------------------------
    # 语义 Memory API（由模型工具调用）
    # ------------------------------------------------------------------

    async def read(self, memory_id: str) -> MemoryRecord | None:
        """读取完整记忆；自动更新 access_count / last_accessed_at。"""

        async with self._lock:
            return await self.store.read(memory_id)

    async def list(self) -> tuple[MemoryRecord, ...]:
        """列出当前 active 记忆（id / title / summary）。"""

        async with self._lock:
            return await self.store.list_active()

    async def create(
        self,
        *,
        title: str,
        summary: str,
        content: str,
    ) -> MemoryRecord:
        """创建普通长期记忆并重建 INDEX。"""

        async with self._lock:
            record = await self.store.create(
                title=title,
                summary=summary,
                content=content,
            )
            await self._rebuild_index()
            return record

    async def update(
        self,
        memory_id: str,
        *,
        content: str,
        reason: str,
    ) -> MemoryRecord:
        """更新已有记忆并重建 INDEX。"""

        async with self._lock:
            record = await self.store.update(
                memory_id,
                content=content,
                reason=reason,
            )
            await self._rebuild_index()
            return record

    async def archive(self, memory_id: str, *, reason: str) -> MemoryRecord:
        """归档记忆并重建 INDEX。"""

        async with self._lock:
            record = await self.store.archive(memory_id, reason=reason)
            await self._rebuild_index()
            return record

    # ------------------------------------------------------------------
    # 容量管理
    # ------------------------------------------------------------------

    async def maintenance_required(self) -> bool:
        """active 数量是否超过上限。"""

        async with self._lock:
            return self.maintenance.exceeds_capacity(
                await self.store.count_active()
            )

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


__all__ = [
    "CORE_MEMORY_MESSAGE_NAME",
    "MEMORY_INDEX_MESSAGE_NAME",
    "MEMORY_POLICY_MESSAGE_NAME",
    "MemoryManager",
]
