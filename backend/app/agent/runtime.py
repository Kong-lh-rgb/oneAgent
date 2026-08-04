"""最小模型与工具执行循环。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from contextlib import suppress
from typing import Any
from uuid import uuid4

from app.models.registry import ModelAdapterRegistry
from app.models.types import (
    Message,
    MessageRole,
    ModelProvider,
    ModelRequest,
    ModelUsage,
    ToolCall,
    ToolResult,
)
from app.tools.approval import (
    ApprovalCallback,
    ApprovalDecision,
    ApprovalGate,
    ApprovalRequest,
)
from app.tools.executor import ToolExecutor
from app.tools.observability import ToolExecutionRecord
from app.tools.registry import ToolRegistry

from .errors import (
    AgentRuntimeError,
    MaxStepsExceededError,
    ModelInvocationError,
    RepeatedToolCallError,
)
from .events import (
    AgentEvent,
    AgentEventHandler,
    AgentEventType,
    CompositeEventHandler,
    NullEventHandler,
)
from .result import (
    AgentError,
    AgentResult,
    AgentStopReason,
    ToolCallRecord,
    ToolRound,
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
        max_output_tokens: int | None = None,
        tool_executor: ToolExecutor | None = None,
        approval_gate: ApprovalGate | None = None,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        if max_output_tokens is not None and max_output_tokens < 1:
            raise ValueError("max_output_tokens must be at least 1")

        self._model_registry = model_registry
        self._tool_registry = tool_registry
        self._provider = provider
        self._model = model
        self._max_steps = max_steps
        self._max_output_tokens = max_output_tokens
        self._tool_executor = tool_executor or ToolExecutor(
            tool_registry,
            approval_gate=approval_gate,
        )

    @property
    def tool_executor(self) -> ToolExecutor:
        return self._tool_executor

    @property
    def tool_records(self) -> tuple[ToolExecutionRecord, ...]:
        """返回工具执行器当前累计保存的观测记录。"""
        return self._tool_executor.execution_records

    async def run(
        self,
        user_input: str,
        *,
        history: Sequence[Message] = (),
        conversation_id: str | None = None,
        event_handler: AgentEventHandler | None = None,
    ) -> AgentResult:
        """处理一次用户输入并返回完整的运行结果（AgentResult）。

        模型和工具运行时错误不会向外抛出，而是以结构化的
        ``AgentResult.error`` 与停止原因返回。
        """

        run_id = uuid4().hex
        emitter = _EventEmitter(
            handler=event_handler or NullEventHandler(),
            run_id=run_id,
            conversation_id=conversation_id,
        )
        user_message = Message(role=MessageRole.USER, content=user_input)
        messages = [*history, user_message]
        previous_signature: str | None = None
        repeated_count = 0
        tool_rounds: list[ToolRound] = []
        tool_calls: list[ToolCallRecord] = []
        usage = ModelUsage()

        await emitter.emit(
            AgentEventType.AGENT_STARTED,
            message=user_message,
            provider=_provider_name(self._provider),
            model=self._model,
        )

        for step in range(1, self._max_steps + 1):
            await emitter.emit(
                AgentEventType.MODEL_STARTED,
                step=step,
                provider=_provider_name(self._provider),
                model=self._model,
            )
            try:
                adapter = self._model_registry.get(self._provider)
                response = await adapter.complete(
                    ModelRequest(
                        messages=tuple(messages),
                        model=self._model,
                        tools=self._tool_registry.definitions(),
                        max_output_tokens=self._max_output_tokens,
                    )
                )
            except Exception as exc:
                error = ModelInvocationError(f"{type(exc).__name__}: {exc}")
                result = self._result(
                    run_id=run_id,
                    final_message=self._error_message(error),
                    messages=messages,
                    steps=step,
                    stop_reason=AgentStopReason.MODEL_ERROR,
                    tool_rounds=tool_rounds,
                    tool_calls=tool_calls,
                    usage=usage,
                    error=error,
                )
                await emitter.emit(
                    AgentEventType.AGENT_FAILED,
                    step=step,
                    provider=_provider_name(self._provider),
                    model=self._model,
                    message=result.final_message,
                    usage=usage,
                    stop_reason=result.stop_reason,
                    error=result.error,
                    result=result,
                )
                return result

            usage = _add_usage(usage, response.usage)
            assistant_message = response.message
            messages.append(assistant_message)
            await emitter.emit(
                AgentEventType.MODEL_COMPLETED,
                step=step,
                provider=response.provider,
                model=response.model,
                message=assistant_message,
                usage=response.usage,
            )
            tool_calls_in_message = assistant_message.tool_calls
            if not tool_calls_in_message:
                result = self._result(
                    run_id=run_id,
                    final_message=assistant_message,
                    messages=messages,
                    steps=step,
                    stop_reason=AgentStopReason.FINAL_ANSWER,
                    tool_rounds=tool_rounds,
                    tool_calls=tool_calls,
                    usage=usage,
                )
                await emitter.emit(
                    AgentEventType.AGENT_COMPLETED,
                    step=step,
                    provider=response.provider,
                    model=response.model,
                    message=assistant_message,
                    usage=usage,
                    stop_reason=result.stop_reason,
                    result=result,
                )
                return result

            round_records: list[ToolCallRecord] = []
            for tool_call in tool_calls_in_message:
                signature = self._tool_call_signature(tool_call)
                if signature == previous_signature:
                    repeated_count += 1
                else:
                    previous_signature = signature
                    repeated_count = 1

                if repeated_count >= 3:
                    error = RepeatedToolCallError(tool_call.name)
                    result = self._result(
                        run_id=run_id,
                        final_message=self._error_message(error),
                        messages=messages,
                        steps=step,
                        stop_reason=AgentStopReason.REPEATED_TOOL_CALL,
                        tool_rounds=tool_rounds,
                        tool_calls=tool_calls,
                        usage=usage,
                        error=error,
                    )
                    await emitter.emit(
                        AgentEventType.AGENT_FAILED,
                        step=step,
                        provider=response.provider,
                        model=response.model,
                        message=result.final_message,
                        tool_call=tool_call,
                        usage=usage,
                        stop_reason=result.stop_reason,
                        error=result.error,
                        result=result,
                    )
                    return result

                await emitter.emit(
                    AgentEventType.TOOL_STARTED,
                    step=step,
                    tool_call=tool_call,
                )

                async def approval_callback(
                    request: ApprovalRequest,
                    decision: ApprovalDecision | None,
                ) -> None:
                    event_type = (
                        AgentEventType.TOOL_APPROVAL_REQUIRED
                        if decision is None
                        else AgentEventType.TOOL_APPROVAL_COMPLETED
                    )
                    await emitter.emit(
                        event_type,
                        step=step,
                        tool_call=tool_call,
                        approval_decision=decision,
                    )

                result = await self._execute_tool(
                    tool_call,
                    approval_callback=approval_callback,
                )
                await emitter.emit(
                    AgentEventType.TOOL_COMPLETED,
                    step=step,
                    tool_call=tool_call,
                    tool_result=result,
                )
                record = ToolCallRecord(
                    round_index=len(tool_rounds),
                    tool_call=tool_call,
                    result=result,
                )
                round_records.append(record)
                tool_calls.append(record)
                messages.append(self._tool_result_message(result))

            tool_rounds.append(
                ToolRound(
                    round_index=len(tool_rounds),
                    assistant_message=assistant_message,
                    records=tuple(round_records),
                )
            )

        error = MaxStepsExceededError(self._max_steps)
        result = self._result(
            run_id=run_id,
            final_message=self._error_message(error),
            messages=messages,
            steps=self._max_steps,
            stop_reason=AgentStopReason.MAX_STEPS,
            tool_rounds=tool_rounds,
            tool_calls=tool_calls,
            usage=usage,
            error=error,
        )
        await emitter.emit(
            AgentEventType.AGENT_FAILED,
            step=self._max_steps,
            provider=_provider_name(self._provider),
            model=self._model,
            message=result.final_message,
            usage=usage,
            stop_reason=result.stop_reason,
            error=result.error,
            result=result,
        )
        return result

    async def run_stream(
        self,
        user_input: str,
        *,
        history: Sequence[Message] = (),
        conversation_id: str | None = None,
        event_handler: AgentEventHandler | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """以事件流方式执行任务，内部复用同一个 ``run()`` 循环。"""

        queue_handler = _QueueEventHandler()
        handler: AgentEventHandler = queue_handler
        if event_handler is not None:
            handler = CompositeEventHandler(queue_handler, event_handler)

        async def execute() -> None:
            try:
                await self.run(
                    user_input,
                    history=history,
                    conversation_id=conversation_id,
                    event_handler=handler,
                )
            finally:
                await queue_handler.finish()

        task = asyncio.create_task(execute())
        try:
            while True:
                item = await queue_handler.next()
                if item is _STREAM_FINISHED:
                    break
                if isinstance(item, AgentEvent):
                    yield item
            await task
        finally:
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    async def _execute_tool(
        self,
        tool_call: ToolCall,
        *,
        approval_callback: ApprovalCallback | None = None,
    ) -> ToolResult:
        try:
            return await self._tool_executor.execute(
                tool_call,
                approval_callback=approval_callback,
            )
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
    def _result(
        *,
        run_id: str,
        final_message: Message,
        messages: Sequence[Message],
        steps: int,
        stop_reason: AgentStopReason,
        tool_rounds: list[ToolRound],
        tool_calls: list[ToolCallRecord],
        usage: ModelUsage,
        error: AgentRuntimeError | None = None,
    ) -> AgentResult:
        complete_messages = tuple(messages)
        if not complete_messages or complete_messages[-1] != final_message:
            complete_messages = (*complete_messages, final_message)

        return AgentResult(
            run_id=run_id,
            final_message=final_message,
            messages=complete_messages,
            steps=steps,
            stop_reason=stop_reason,
            tool_rounds=tuple(tool_rounds),
            tool_calls=tuple(tool_calls),
            usage=usage,
            error=(
                AgentError(type=type(error).__name__, message=str(error))
                if error is not None
                else None
            ),
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


def _add_usage(total: ModelUsage, current: ModelUsage) -> ModelUsage:
    """累加多轮模型调用的 token 用量。"""

    return ModelUsage(
        input_tokens=total.input_tokens + current.input_tokens,
        output_tokens=total.output_tokens + current.output_tokens,
        total_tokens=total.total_tokens + current.total_tokens,
    )


class _EventEmitter:
    """为单次运行补充公共标识、顺序并隔离处理器异常。"""

    def __init__(
        self,
        *,
        handler: AgentEventHandler,
        run_id: str,
        conversation_id: str | None,
    ) -> None:
        self._handler = handler
        self._run_id = run_id
        self._conversation_id = conversation_id
        self._sequence = 0

    async def emit(
        self,
        event_type: AgentEventType,
        **payload: Any,
    ) -> None:
        event = AgentEvent(
            run_id=self._run_id,
            conversation_id=self._conversation_id,
            sequence=self._sequence,
            type=event_type,
            **payload,
        )
        self._sequence += 1
        try:
            await self._handler.emit(event)
        except Exception:
            # 事件观察者故障不能中断 Agent 的核心执行流程。
            return


def _provider_name(provider: ModelProvider | str | None) -> str | None:
    if isinstance(provider, ModelProvider):
        return provider.value
    return provider


_STREAM_FINISHED = object()


class _QueueEventHandler(AgentEventHandler):
    """把 Runtime 回调事件转交给异步迭代器。"""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[AgentEvent | object] = asyncio.Queue(maxsize=100)

    async def emit(self, event: AgentEvent) -> None:
        await self._queue.put(event)

    async def finish(self) -> None:
        await self._queue.put(_STREAM_FINISHED)

    async def next(self) -> AgentEvent | object:
        return await self._queue.get()
