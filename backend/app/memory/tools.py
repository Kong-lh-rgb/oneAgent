"""长期记忆语义化工具：供模型主动 Recall / Create / Update / Archive。

这些工具不是数据库 CRUD，而是语义化 Memory API。读取是显式的
（Model-directed Recall）：模型看到 Recall Cue 后决定何时 ``memory.read``。
Runtime 不做 query-driven 自动检索或 Top-K 注入。
"""

from __future__ import annotations

from typing import Any

from app.models.types import ToolDefinition

from ..tools.base import BaseTool
from ..tools.registry import ToolRegistry
from .manager import MemoryManager
from .prompts import MEMORY_WRITE_POLICY


class MemoryReadTool(BaseTool):
    """读取一条完整长期记忆。"""

    def __init__(self, manager: MemoryManager) -> None:
        self._manager = manager

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="memory.read",
            description=(
                "读取一条完整长期记忆。仅当 Memory Index 中的某个 cue 与当前任务"
                "明显相关时才调用；不要无谓读取。每次读取会记录访问次数。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "memory_id": {
                        "type": "string",
                        "description": "要读取的 Memory ID，例如 M001。",
                    },
                },
                "required": ["memory_id"],
                "additionalProperties": False,
            },
            strict=False,
        )

    async def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        memory_id = arguments.get("memory_id")
        if not isinstance(memory_id, str) or not memory_id.strip():
            raise ValueError("'memory_id' must be a non-empty string")
        record = await self._manager.read(memory_id)
        if record is None:
            return {"found": False, "memory_id": memory_id}
        return {
            "found": True,
            "id": record.id,
            "title": record.title,
            "content": record.render_full(),
        }


class MemoryListTool(BaseTool):
    """列出当前 active 长期记忆的简要信息。"""

    def __init__(self, manager: MemoryManager) -> None:
        self._manager = manager

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="memory.list",
            description=(
                "列出当前 active 长期记忆的 id、标题与摘要（Recall Cue）。"
                "不返回完整正文；需要详情时用 memory.read。"
            ),
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            strict=False,
        )

    async def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        records = await self._manager.list()
        return {
            "memories": [
                {
                    "id": record.id,
                    "title": record.title,
                    "summary": record.summary,
                }
                for record in records
            ]
        }


class MemoryCreateTool(BaseTool):
    """创建一条普通长期记忆。"""

    def __init__(self, manager: MemoryManager) -> None:
        self._manager = manager

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="memory.create",
            description=(
                "创建一条长期记忆。只有对未来跨会话仍有明显价值的信息才创建；"
                "当前任务状态属于 Task、可复用流程属于 Skills，都不应写入。"
                f"写入条件：{MEMORY_WRITE_POLICY}"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "记忆标题。",
                    },
                    "summary": {
                        "type": "string",
                        "description": "Recall Cue，用于 Index 中提示模型何时读取。",
                    },
                    "content": {
                        "type": "string",
                        "description": "记忆完整正文。",
                    },
                },
                "required": ["title", "summary", "content"],
                "additionalProperties": False,
            },
            strict=False,
        )

    async def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        title = arguments.get("title")
        summary = arguments.get("summary")
        content = arguments.get("content")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("'title' must be a non-empty string")
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("'summary' must be a non-empty string")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("'content' must be a non-empty string")
        record = await self._manager.create(
            title=title,
            summary=summary,
            content=content,
        )
        result: dict[str, Any] = {
            "id": record.id,
            "title": record.title,
            "summary": record.summary,
        }
        if await self._manager.maintenance_required():
            candidates = await self._manager.retention_candidates()
            result["maintenance_required"] = True
            result["candidates"] = [
                {
                    "id": item.id,
                    "title": item.title,
                    "access_count": item.access_count,
                }
                for item in candidates
            ]
            result["maintenance_instruction"] = (
                "active memory exceeds capacity. Choose KEEP, MERGE or ARCHIVE "
                "for each candidate, and act with memory.update / memory.archive."
            )
        return result


class MemoryUpdateTool(BaseTool):
    """修正已有长期记忆。"""

    def __init__(self, manager: MemoryManager) -> None:
        self._manager = manager

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="memory.update",
            description=(
                "修正一条已有长期记忆的正文。当新信息只是旧记忆的更新时应优先"
                "使用 update 而不是 create 新记忆，避免产生重复记忆。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "memory_id": {
                        "type": "string",
                        "description": "要更新的 Memory ID。",
                    },
                    "content": {
                        "type": "string",
                        "description": "修正后的完整正文。",
                    },
                    "reason": {
                        "type": "string",
                        "description": "更新原因（留痕）。",
                    },
                },
                "required": ["memory_id", "content", "reason"],
                "additionalProperties": False,
            },
            strict=False,
        )

    async def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        memory_id = arguments.get("memory_id")
        content = arguments.get("content")
        reason = arguments.get("reason")
        if not isinstance(memory_id, str) or not memory_id.strip():
            raise ValueError("'memory_id' must be a non-empty string")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("'content' must be a non-empty string")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("'reason' must be a non-empty string")
        record = await self._manager.update(
            memory_id,
            content=content,
            reason=reason,
        )
        return {"id": record.id, "updated": True}


class MemoryArchiveTool(BaseTool):
    """把过时长期记忆归档。"""

    def __init__(self, manager: MemoryManager) -> None:
        self._manager = manager

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="memory.archive",
            description=(
                "把一条过时或不再需要的长期记忆归档。归档后不再出现在 Memory "
                "Index 中，也不会进入模型上下文。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "memory_id": {
                        "type": "string",
                        "description": "要归档的 Memory ID。",
                    },
                    "reason": {
                        "type": "string",
                        "description": "归档原因（留痕）。",
                    },
                },
                "required": ["memory_id", "reason"],
                "additionalProperties": False,
            },
            strict=False,
        )

    async def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        memory_id = arguments.get("memory_id")
        reason = arguments.get("reason")
        if not isinstance(memory_id, str) or not memory_id.strip():
            raise ValueError("'memory_id' must be a non-empty string")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("'reason' must be a non-empty string")
        record = await self._manager.archive(memory_id, reason=reason)
        return {"id": record.id, "status": record.status.value}


def register_memory_tools(
    registry: ToolRegistry,
    manager: MemoryManager,
) -> None:
    """把长期记忆语义工具注册到本地工具注册表。"""

    registry.register(MemoryReadTool(manager))
    registry.register(MemoryListTool(manager))
    registry.register(MemoryCreateTool(manager))
    registry.register(MemoryUpdateTool(manager))
    registry.register(MemoryArchiveTool(manager))


__all__ = [
    "MemoryArchiveTool",
    "MemoryCreateTool",
    "MemoryListTool",
    "MemoryReadTool",
    "MemoryUpdateTool",
    "register_memory_tools",
]
