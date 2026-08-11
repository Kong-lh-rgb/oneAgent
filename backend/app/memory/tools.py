"""长期记忆语义化工具：在线 Recall、显式 Core 操作与内部普通写入。

这些工具不是数据库 CRUD，而是语义化 Memory API。读取是显式的
（Model-directed Recall）：模型看到 Recall Cue 后决定何时 ``memory.read``。
Runtime 不做 query-driven 自动检索或 Top-K 注入。普通 Memory 写工具保留为
内部能力，但不注册到 Main Agent 的默认 Tool Registry。
"""

from __future__ import annotations

from typing import Any

from app.models.types import ToolDefinition

from ..tools.base import BaseTool
from ..tools.hooks import ToolExecutionContext
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


class CoreMemoryUpdateTool(BaseTool):
    """根据当前用户的明确长期陈述，按 key 创建或更新 Core Memory。"""

    def __init__(self, manager: MemoryManager) -> None:
        self._manager = manager

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="core_memory.update",
            description=(
                "按稳定 key 创建或更新一条 Core Memory。是否属于 Core 由你判断，"
                "但只能用于当前用户明确表达的稳定身份、全局长期偏好或跨任务长期"
                "约束；项目背景和重要历史决定由 Run 后的 Memory Reflection 处理，"
                "当前任务状态属于 Task，可复用流程属于 Skills。必须逐字复制当前"
                "用户消息中的明确原话作为 explicit_user_statement；不要根据推断"
                "调用。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": (
                            "稳定的小写点分 key，例如 communication.language。"
                        ),
                    },
                    "value": {
                        "type": "string",
                        "description": "应在每次 Run 常驻的精简长期事实或约束。",
                    },
                    "reason": {
                        "type": "string",
                        "description": "为什么该信息属于 Core，以及本次更新原因。",
                    },
                    "explicit_user_statement": {
                        "type": "string",
                        "description": "从当前用户消息逐字复制的明确长期陈述。",
                    },
                },
                "required": [
                    "key",
                    "value",
                    "reason",
                    "explicit_user_statement",
                ],
                "additionalProperties": False,
            },
            strict=False,
        )

    async def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        raise ValueError("core_memory.update requires the current user message")

    async def execute_with_context(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        key = _required_string(arguments, "key")
        value = _required_string(arguments, "value")
        reason = _required_string(arguments, "reason")
        statement = _required_string(arguments, "explicit_user_statement")
        user_input = context.user_input
        if not user_input or statement not in user_input:
            raise ValueError(
                "'explicit_user_statement' must be copied exactly from the "
                "current user message"
            )
        entry, created = await self._manager.upsert_core(
            key=key,
            value=value,
            reason=reason,
            source_statement=statement,
        )
        return {
            "key": entry.key,
            "value": entry.value,
            "created": created,
            "updated": not created,
        }


class CoreMemoryRemoveTool(BaseTool):
    """根据当前用户明确要求移除一个按 key 管理的 Core 条目。"""

    def __init__(self, manager: MemoryManager) -> None:
        self._manager = manager

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="core_memory.remove",
            description=(
                "移除一个不再成立的 Core Memory 条目。只能在当前用户明确撤销"
                "身份、全局长期偏好或跨任务约束时调用；必须逐字复制当前用户"
                "消息中的撤销原话，不要根据推断或旧消息移除。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "要移除的稳定小写点分 key。",
                    },
                    "reason": {
                        "type": "string",
                        "description": "移除原因（会保留在 Tool/Trace 记录中）。",
                    },
                    "explicit_user_statement": {
                        "type": "string",
                        "description": "从当前用户消息逐字复制的明确撤销原话。",
                    },
                },
                "required": ["key", "reason", "explicit_user_statement"],
                "additionalProperties": False,
            },
            strict=False,
        )

    async def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        raise ValueError("core_memory.remove requires the current user message")

    async def execute_with_context(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        key = _required_string(arguments, "key")
        _required_string(arguments, "reason")
        statement = _required_string(arguments, "explicit_user_statement")
        user_input = context.user_input
        if not user_input or statement not in user_input:
            raise ValueError(
                "'explicit_user_statement' must be copied exactly from the "
                "current user message"
            )
        removed = await self._manager.remove_core(key)
        return {"key": removed.key, "removed": True}


def register_memory_tools(
    registry: ToolRegistry,
    manager: MemoryManager,
) -> None:
    """注册 Main Agent 的在线 Recall 与显式 Core 操作。"""

    registry.register(MemoryReadTool(manager))
    registry.register(MemoryListTool(manager))
    registry.register(CoreMemoryUpdateTool(manager))
    registry.register(CoreMemoryRemoveTool(manager))


def register_memory_write_tools(
    registry: ToolRegistry,
    manager: MemoryManager,
) -> None:
    """为离线测试或未来内部 Agent 注册普通 Memory mutation 工具。"""

    registry.register(MemoryCreateTool(manager))
    registry.register(MemoryUpdateTool(manager))
    registry.register(MemoryArchiveTool(manager))


def _required_string(arguments: dict[str, Any], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{name}' must be a non-empty string")
    return value.strip()


__all__ = [
    "CoreMemoryRemoveTool",
    "CoreMemoryUpdateTool",
    "MemoryArchiveTool",
    "MemoryCreateTool",
    "MemoryListTool",
    "MemoryReadTool",
    "MemoryUpdateTool",
    "register_memory_tools",
    "register_memory_write_tools",
]
