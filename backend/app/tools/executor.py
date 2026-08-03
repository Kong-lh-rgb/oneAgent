"""本地工具的安全执行边界。"""

from __future__ import annotations

import asyncio
import json
from time import perf_counter
from typing import Any

from app.models.types import ToolCall, ToolResult

from .registry import ToolRegistry

MAX_TOOL_OUTPUT_CHARS = 20_000


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        timeout_seconds: float = 30.0,
        max_output_chars: int = MAX_TOOL_OUTPUT_CHARS,
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

    async def execute(self, tool_call: ToolCall) -> ToolResult:
        started_at = perf_counter()

        try:
            tool = self._registry.get(tool_call.name)
        except KeyError:
            return self._failure(
                tool_call,
                f"Tool not found: {tool_call.name}",
                started_at,
            )

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


def _serialize_output(output: Any) -> str:
    if isinstance(output, str):
        return output
    try:
        return json.dumps(output, ensure_ascii=False, separators=(",", ":"))
    except TypeError, ValueError:
        return str(output)


def _truncate(value: str, limit: int) -> str:
    return value[:limit]


def _duration_ms(started_at: float) -> float:
    return max(0.0, (perf_counter() - started_at) * 1000)
