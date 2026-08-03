"""本地工具的安全执行边界。

包含三道防线：
- 权限控制：ALLOWED 直接执行；HUMAN_APPROVAL 需人工审核；FORBIDDEN 拒绝。
- 资源限制：超时、输出截断、参数必须是 JSON 对象。
- 可观测性：每次执行（含失败）都会写入 ToolExecutionLogger。
"""

from __future__ import annotations

import asyncio
import json
from time import perf_counter
from typing import Any

from app.models.types import ToolCall, ToolPermission, ToolResult

from .approval import ApprovalDecision, ApprovalGate, ApprovalRequest, DenyAllGate
from .base import BaseTool
from .observability import (
    InMemoryExecutionLogger,
    ToolExecutionLogger,
    ToolExecutionRecord,
    _now_iso,
)
from .registry import ToolRegistry

MAX_TOOL_OUTPUT_CHARS = 20_000


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        timeout_seconds: float = 30.0,
        max_output_chars: int = MAX_TOOL_OUTPUT_CHARS,
        approval_gate: ApprovalGate | None = None,
        logger: ToolExecutionLogger | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if max_output_chars <= 0:
            raise ValueError("max_output_chars must be greater than zero")
        if max_output_chars > MAX_TOOL_OUTPUT_CHARS:
            raise ValueError(f"max_output_chars cannot exceed {MAX_TOOL_OUTPUT_CHARS}")
        self._registry = registry
        self._timeout_seconds = timeout_seconds
        self._max_output_chars = max_output_chars
        self._approval_gate = approval_gate or DenyAllGate()
        self.logger = logger or InMemoryExecutionLogger()

    @property
    def execution_records(self) -> tuple[ToolExecutionRecord, ...]:
        """最近的执行记录（仅当使用 InMemoryExecutionLogger 时可用）。"""
        if isinstance(self.logger, InMemoryExecutionLogger):
            return self.logger.records
        return ()

    async def execute(self, tool_call: ToolCall) -> ToolResult:
        started_at = perf_counter()
        started_iso = _now_iso()

        tool = self._lookup_tool(tool_call)
        if tool is None:
            result = self._failure(
                tool_call,
                f"Tool not found: {tool_call.name}",
                started_at,
            )
            self._emit(tool_call, ToolPermission.ALLOWED, result, started_iso)
            return result

        permission = tool.definition.permission
        denied_reason = await self._authorize(tool, tool_call)
        if denied_reason is not None:
            result = self._failure(tool_call, denied_reason, started_at)
            self._emit(tool_call, permission, result, started_iso)
            return result

        result = await self._dispatch(tool, tool_call, started_at)
        self._emit(tool_call, permission, result, started_iso)
        return result

    def _lookup_tool(self, tool_call: ToolCall) -> BaseTool | None:
        try:
            return self._registry.get(tool_call.name)
        except KeyError:
            return None

    async def _authorize(self, tool: BaseTool, tool_call: ToolCall) -> str | None:
        """返回被拒绝的原因；允许执行时返回 None。"""
        permission = tool.definition.permission
        if permission is ToolPermission.FORBIDDEN:
            return f"Tool '{tool_call.name}' is forbidden for model execution."
        if permission is ToolPermission.HUMAN_APPROVAL:
            request = ApprovalRequest(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                arguments=_safe_arguments(tool_call.arguments),
                description=tool.definition.description,
            )
            decision = await self._approval_gate.request_approval(request)
            if decision is not ApprovalDecision.APPROVED:
                return (
                    f"Tool '{tool_call.name}' execution was denied "
                    "(requires human approval)."
                )
        return None

    async def _dispatch(
        self,
        tool: BaseTool,
        tool_call: ToolCall,
        started_at: float,
    ) -> ToolResult:
        try:
            arguments = _parse_arguments(tool_call.arguments)
        except (TypeError, ValueError) as exc:
            return self._failure(
                tool_call,
                f"Invalid arguments: {exc}",
                started_at,
            )

        try:
            async with asyncio.timeout(self._timeout_seconds):
                output = await tool.execute(arguments)
        except TimeoutError:
            return self._failure(
                tool_call,
                f"Tool timed out after {self._timeout_seconds:g} seconds.",
                started_at,
            )
        except (KeyError, TypeError, ValueError) as exc:
            return self._failure(
                tool_call,
                f"Invalid arguments: {exc}",
                started_at,
            )
        except Exception as exc:
            return self._failure(
                tool_call,
                f"Tool execution failed: {type(exc).__name__}: {exc}",
                started_at,
            )

        return ToolResult(
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            success=True,
            output=_truncate(_serialize_output(output), self._max_output_chars),
            error=None,
            duration_ms=_duration_ms(started_at),
        )

    def _failure(
        self,
        tool_call: ToolCall,
        error: str,
        started_at: float,
    ) -> ToolResult:
        return ToolResult(
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            success=False,
            output=None,
            error=error,
            duration_ms=_duration_ms(started_at),
        )

    def _emit(
        self,
        tool_call: ToolCall,
        permission: ToolPermission,
        result: ToolResult,
        started_iso: str,
    ) -> None:
        record = ToolExecutionRecord(
            tool_call_id=result.tool_call_id,
            tool_name=result.tool_name,
            permission=permission,
            started_at=started_iso,
            duration_ms=result.duration_ms,
            success=result.success,
            output=result.output,
            error=result.error,
        )
        self.logger.record(record)


def _parse_arguments(arguments: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(arguments, dict):
        return arguments
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError as exc:
        raise ValueError(f"arguments are not valid JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise TypeError("arguments must be a JSON object")
    return parsed


def _safe_arguments(arguments: dict[str, Any] | str) -> dict[str, Any]:
    """用于审批展示的参数；解析失败时返回空字典而不是抛错。"""
    try:
        return _parse_arguments(arguments)
    except (TypeError, ValueError):
        return {}


def _serialize_output(output: Any) -> str:
    if isinstance(output, str):
        return output
    try:
        return json.dumps(output, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(output)


def _truncate(value: str, limit: int) -> str:
    return value[:limit]


def _duration_ms(started_at: float) -> float:
    return max(0.0, (perf_counter() - started_at) * 1000)
