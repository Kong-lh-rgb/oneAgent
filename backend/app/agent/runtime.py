"""最小模型与工具执行循环。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from app.models.registry import ModelAdapterRegistry
from app.models.types import (
    Message,
    MessageRole,
    ModelProvider,
    ModelRequest,
    ToolCall,
    ToolResult,
)
from app.tools.approval import ApprovalGate
from app.tools.executor import ToolExecutor
from app.tools.observability import ToolExecutionRecord
from app.tools.registry import ToolRegistry

from .errors import (
    AgentRuntimeError,
    MaxStepsExceededError,
    ModelInvocationError,
    RepeatedToolCallError,
)


class AgentRuntime:
    """运行模型，直到返回最终消息或循环必须停止。"""

    def __init__(
        self,
        model_registry: ModelAdapterRegistry,
        tool_registry: ToolRegistry,
        *,
        provider: ModelProvider | str | None = None,
        model: str | None = None,
        max_steps: int = 10,
        tool_executor: ToolExecutor | None = None,
        approval_gate: ApprovalGate | None = None,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be at least 1")

        self._model_registry = model_registry
        self._tool_registry = tool_registry
        self._provider = provider
        self._model = model
        self._max_steps = max_steps
        self._tool_executor = tool_executor or ToolExecutor(
            tool_registry,
            approval_gate=approval_gate,
        )

    @property
    def tool_executor(self) -> ToolExecutor:
        return self._tool_executor

    @property
    def tool_records(self) -> tuple[ToolExecutionRecord, ...]:
        """最近一次 run() 中所有工具执行的观测记录。"""
        return self._tool_executor.execution_records

    async def run(
        self,
        user_input: str,
        *,
        history: Sequence[Message] = (),
    ) -> Message:
        """处理一次用户输入并返回最终的助手消息。

        模型和工具运行时错误会转换为对话消息，
        不会向外抛出并导致调用方进程终止。
        """

        messages = [*history, Message(role=MessageRole.USER, content=user_input)]
        previous_signature: str | None = None
        repeated_count = 0

        for _ in range(self._max_steps):
            try:
                adapter = self._model_registry.get(self._provider)
                response = await adapter.complete(
                    ModelRequest(
                        messages=tuple(messages),
                        model=self._model,
                        tools=self._tool_registry.definitions(),
                    )
                )
            except Exception as exc:
                return self._error_message(
                    ModelInvocationError(f"{type(exc).__name__}: {exc}")
                )

            assistant_message = response.message
            messages.append(assistant_message)
            tool_calls = assistant_message.tool_calls
            if not tool_calls:
                return assistant_message

            for tool_call in tool_calls:
                signature = self._tool_call_signature(tool_call)
                if signature == previous_signature:
                    repeated_count += 1
                else:
                    previous_signature = signature
                    repeated_count = 1

                if repeated_count >= 3:
                    return self._error_message(
                        RepeatedToolCallError(tool_call.name)
                    )

                result = await self._execute_tool(tool_call)
                messages.append(self._tool_result_message(result))

        return self._error_message(MaxStepsExceededError(self._max_steps))

    async def _execute_tool(self, tool_call: ToolCall) -> ToolResult:
        try:
            return await self._tool_executor.execute(tool_call)
        except Exception as exc:
            return ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=0.0,
            )

    @staticmethod
    def _tool_result_message(result: ToolResult) -> Message:
        return Message(
            role=MessageRole.TOOL,
            name=result.tool_name,
            tool_call_id=result.tool_call_id,
            content=result.model_dump_json(exclude_none=True),
        )

    @staticmethod
    def _error_message(error: AgentRuntimeError) -> Message:
        return Message(
            role=MessageRole.ASSISTANT,
            content=f"Agent stopped: {error}",
        )

    @staticmethod
    def _tool_call_signature(tool_call: ToolCall) -> str:
        arguments: Any = tool_call.arguments
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                return f"{tool_call.name}:{arguments}"

        canonical_arguments = json.dumps(
            arguments,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return f"{tool_call.name}:{canonical_arguments}"
