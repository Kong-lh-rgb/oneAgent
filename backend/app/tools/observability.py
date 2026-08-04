"""工具执行的可观测性：记录每次执行的成败、耗时与错误原因。

- ``ToolExecutionRecord``: 单次执行的统一记录。
- ``InMemoryExecutionLogger``: 内存环形缓冲，便于查询最近记录（供未来 API/UI 使用）。
- ``StructLogExecutionLogger``: 通过 structlog 输出到日志。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import structlog

from app.models.types import ToolPermission, ToolResult

from .hooks import ToolExecutionContext, ToolHook


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class ToolExecutionRecord:
    """单次工具执行的不可变记录。"""

    __slots__ = (
        "id",
        "tool_call_id",
        "tool_name",
        "permission",
        "started_at",
        "duration_ms",
        "success",
        "output",
        "error",
    )

    def __init__(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        permission: ToolPermission | str,
        started_at: str,
        duration_ms: float,
        success: bool,
        output: str | None = None,
        error: str | None = None,
    ) -> None:
        self.id = uuid4().hex
        self.tool_call_id = tool_call_id
        self.tool_name = tool_name
        self.permission = (
            permission.value if isinstance(permission, ToolPermission) else permission
        )
        self.started_at = started_at
        self.duration_ms = round(duration_ms, 3)
        self.success = success
        self.output = output
        self.error = error

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "permission": self.permission,
            "started_at": self.started_at,
            "duration_ms": self.duration_ms,
            "success": self.success,
            "output": self.output,
            "error": self.error,
        }


class ToolExecutionLogger(ABC):
    """工具执行记录的接收者。"""

    @abstractmethod
    def record(self, record: ToolExecutionRecord) -> None:
        """记录一次工具执行。"""


class InMemoryExecutionLogger(ToolExecutionLogger):
    """把最近 N 条执行记录保存在内存中。"""

    def __init__(self, maxlen: int = 200) -> None:
        if maxlen < 1:
            raise ValueError("maxlen must be at least 1")
        self._maxlen = maxlen
        self._records: list[ToolExecutionRecord] = []

    def record(self, record: ToolExecutionRecord) -> None:
        self._records.append(record)
        if len(self._records) > self._maxlen:
            del self._records[: len(self._records) - self._maxlen]

    @property
    def records(self) -> tuple[ToolExecutionRecord, ...]:
        return tuple(self._records)

    def recent(self, limit: int = 10) -> tuple[ToolExecutionRecord, ...]:
        return tuple(self._records[-limit:])

    @property
    def count(self) -> int:
        return len(self._records)

    def clear(self) -> None:
        self._records.clear()


class StructLogExecutionLogger(ToolExecutionLogger):
    """通过 structlog 输出执行记录，失败时记录 error 原因。"""

    def __init__(self, logger_name: str = "oneagent.tools") -> None:
        self._log = structlog.get_logger(logger_name)

    def record(self, record: ToolExecutionRecord) -> None:
        event = record.to_dict()
        if record.success:
            self._log.info("tool.executed", **event)
        else:
            self._log.warning(
                "tool.failed",
                **event,
                error_reason=record.error,
            )


class ObservabilityHook(ToolHook):
    """在工具执行结束后写入统一执行记录。"""

    def __init__(self, logger: ToolExecutionLogger) -> None:
        self._logger = logger

    async def after_execute(
        self,
        context: ToolExecutionContext,
        result: ToolResult,
    ) -> None:
        permission = (
            context.tool_definition.permission
            if context.tool_definition is not None
            else ToolPermission.ALLOWED
        )
        record = ToolExecutionRecord(
            tool_call_id=result.tool_call_id,
            tool_name=result.tool_name,
            permission=permission,
            started_at=str(context.metadata["started_at"]),
            duration_ms=result.duration_ms,
            success=result.success,
            output=result.output,
            error=result.error,
        )
        self._logger.record(record)


__all__ = [
    "InMemoryExecutionLogger",
    "ObservabilityHook",
    "StructLogExecutionLogger",
    "ToolExecutionLogger",
    "ToolExecutionRecord",
]
