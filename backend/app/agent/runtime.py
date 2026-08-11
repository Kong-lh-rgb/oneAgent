"""最小模型与工具执行循环。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from contextlib import suppress
from typing import Any
from uuid import uuid4

from app.checkpoint import (
    RunCheckpoint,
    SQLiteCheckpointStore,
    render_checkpoint_context,
)
from app.context import ContextManager, ConversationSummaryState
from app.memory import MemoryManager, MemoryReflectionInput, PostRunMemoryReflector
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
from app.task.context import TaskContextProvider
from app.tools.approval import ApprovalGate
from app.tools.executor import ToolExecutor
from app.tools.hooks import ToolExecutionContext, ToolHook
from app.tools.observability import ToolExecutionRecord
from app.tools.permissions.policy import PermissionPolicyEngine
from app.tools.permissions.store import PermissionRuleStore
from app.tools.registry import ToolRegistry

from .errors import (
    AgentRuntimeError,
    ContextPreparationError,
    ContextWindowExceededError,
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
from .tool_hooks import AgentEventHook


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
        max_tool_rounds: int | None = None,
        max_output_tokens: int | None = None,
        tool_executor: ToolExecutor | None = None,
        approval_gate: ApprovalGate | None = None,
        policy_engine: PermissionPolicyEngine | None = None,
        rule_store: PermissionRuleStore | None = None,
        context_manager: ContextManager | None = None,
        task_context_provider: TaskContextProvider | None = None,
        checkpoint_store: SQLiteCheckpointStore | None = None,
        memory_manager: MemoryManager | None = None,
        memory_reflector: PostRunMemoryReflector | None = None,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        if max_output_tokens is not None and max_output_tokens < 1:
            raise ValueError("max_output_tokens must be at least 1")
        if max_tool_rounds is not None and max_tool_rounds < 1:
            raise ValueError("max_tool_rounds must be at least 1")
        if memory_reflector is not None and memory_manager is None:
            raise ValueError("memory_reflector requires memory_manager")

        self._model_registry = model_registry
        self._tool_registry = tool_registry
        self._provider = provider
        self._model = model
        self._max_steps = max_steps
        self._max_tool_rounds = max_tool_rounds
        self._max_output_tokens = max_output_tokens
        self._context_manager = context_manager or ContextManager()
        self._task_context_provider = task_context_provider
        self._checkpoint_store = checkpoint_store
        self._memory_manager = memory_manager
        self._memory_reflector = memory_reflector
        self._tool_executor = tool_executor or ToolExecutor(
            tool_registry,
            approval_gate=approval_gate,
            policy_engine=policy_engine,
            rule_store=rule_store,
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
        summary_state: ConversationSummaryState | None = None,
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
        try:
            recovery_checkpoint: RunCheckpoint | None = None
            if self._checkpoint_store is not None:
                if conversation_id is not None:
                    recovery_checkpoint = (
                        await self._checkpoint_store.latest_unrecovered(
                            conversation_id
                        )
                    )
                await self._checkpoint_store.start(
                    run_id,
                    conversation_id=conversation_id,
                    user_message=Message(
                        role=MessageRole.USER,
                        content=user_input,
                    ),
                )
            try:
                result = await self._run_once(
                    run_id,
                    user_input,
                    history=history,
                    conversation_id=conversation_id,
                    emitter=emitter,
                    summary_state=summary_state,
                    recovery_checkpoint=recovery_checkpoint,
                )
            except BaseException as exc:
                if self._checkpoint_store is not None:
                    with suppress(Exception):
                        await self._checkpoint_store.interrupt(
                            run_id,
                            error=f"{type(exc).__name__}: {exc}",
                        )
                raise

            if self._checkpoint_store is not None:
                if result.ok:
                    await self._checkpoint_store.complete(
                        run_id,
                        stop_reason=result.stop_reason,
                    )
                    if recovery_checkpoint is not None:
                        with suppress(Exception):
                            await self._checkpoint_store.mark_recovered(
                                recovery_checkpoint.run_id,
                                recovered_by_run_id=run_id,
                            )
                else:
                    await self._checkpoint_store.fail(
                        run_id,
                        stop_reason=result.stop_reason,
                        error=(
                            result.error.message
                            if result.error is not None
                            else None
                        ),
                    )
            await self._run_post_run_memory_reflection(
                result,
                user_input=user_input,
                conversation_id=conversation_id,
                emitter=emitter,
            )
            await emitter.emit(
                (
                    AgentEventType.AGENT_COMPLETED
                    if result.ok
                    else AgentEventType.AGENT_FAILED
                ),
                step=result.steps or None,
                message=result.final_message,
                usage=result.usage,
                stop_reason=result.stop_reason,
                error=result.error,
                result=result,
            )
            return result
        finally:
            with suppress(Exception):
                await self._tool_executor.clear_run_rules(run_id)

    async def _run_once(
        self,
        run_id: str,
        user_input: str,
        *,
        history: Sequence[Message],
        conversation_id: str | None,
        emitter: _EventEmitter,
        summary_state: ConversationSummaryState | None,
        recovery_checkpoint: RunCheckpoint | None,
    ) -> AgentResult:
        """执行一次已分配 Run ID 的 Agent 循环。"""

        tool_event_hook = AgentEventHook(emitter)
        user_message = Message(role=MessageRole.USER, content=user_input)
        # 原始消息用于 AgentResult 和数据库持久化，始终保留完整工具协议。
        historical_message_count = len(history)
        messages = [*history, user_message]
        previous_signature: str | None = None
        repeated_count = 0
        tool_rounds: list[ToolRound] = []
        tool_calls: list[ToolCallRecord] = []
        usage = ModelUsage()
        current_summary_state = summary_state
        memory_context_messages: tuple[Message, ...] = ()
        memory_context_loaded = False

        await emitter.emit(
            AgentEventType.AGENT_STARTED,
            message=user_message,
            provider=_provider_name(self._provider),
            model=self._model,
        )

        async def stop_with_error(
            error: AgentRuntimeError,
            stop_reason: AgentStopReason,
            *,
            step: int,
        ) -> AgentResult:
            """构造失败结果；公共 run() 在后置阶段结束后发射终止事件。"""

            return self._result(
                run_id=run_id,
                final_message=self._error_message(error),
                messages=messages,
                steps=step,
                stop_reason=stop_reason,
                tool_rounds=tool_rounds,
                tool_calls=tool_calls,
                usage=usage,
                error=error,
                summary_state=current_summary_state,
            )

        for step in range(1, self._max_steps + 1):
            if self._checkpoint_store is not None:
                await self._checkpoint_store.before_model(run_id, step=step)
            force_final_answer = (
                self._max_tool_rounds is not None
                and len(tool_rounds) >= self._max_tool_rounds
            )
            request_messages = tuple(messages)
            request_tools = (
                () if force_final_answer else self._tool_registry.definitions()
            )
            # 先解析实际使用的模型和输出上限，确保预算与请求完全一致。
            try:
                adapter = self._model_registry.get(self._provider)
                resolved_model = self._model or adapter.default_model
                resolved_provider = adapter.provider
                effective_max_output_tokens = (
                    self._max_output_tokens or adapter.config.default_max_output_tokens
                )
            except Exception as exc:
                return await stop_with_error(
                    ModelInvocationError(f"{type(exc).__name__}: {exc}"),
                    AgentStopReason.MODEL_ERROR,
                    step=step,
                )

            try:
                ephemeral_messages: list[Message] = []
                if self._memory_manager is not None and not memory_context_loaded:
                    memory_context_loaded = True
                    try:
                        memory_context_messages = (
                            await self._memory_manager.context_messages()
                        )
                    except Exception:
                        memory_context_messages = ()
                if memory_context_messages:
                    ephemeral_messages.extend(memory_context_messages)
                if recovery_checkpoint is not None:
                    ephemeral_messages.append(
                        render_checkpoint_context(recovery_checkpoint)
                    )
                if self._task_context_provider is not None:
                    task_message = await self._task_context_provider.message_for(
                        conversation_id
                    )
                    if task_message is not None:
                        ephemeral_messages.append(task_message)
                if ephemeral_messages:
                    request_messages = (
                        *request_messages[:historical_message_count],
                        *ephemeral_messages,
                        *request_messages[historical_message_count:],
                    )
                if force_final_answer:
                    request_messages = (
                        *request_messages,
                        Message(
                            role=MessageRole.SYSTEM,
                            content=(
                                "工具调用轮次已用完。请停止调用工具，直接根据已经"
                                "获得的信息回答用户；如果信息有限，请明确说明，不要"
                                "继续搜索。"
                            ),
                        ),
                    )
            except Exception as exc:
                return await stop_with_error(
                    ContextPreparationError(f"{type(exc).__name__}: {exc}"),
                    AgentStopReason.CONTEXT_ERROR,
                    step=step,
                )

            try:
                context_decision = await self._context_manager.prepare(
                    request_messages,
                    tools=request_tools,
                    model=resolved_model,
                    provider=resolved_provider,
                    max_output_tokens=effective_max_output_tokens,
                    history_count=historical_message_count,
                    summary_state=current_summary_state,
                )
            except Exception as exc:
                return await stop_with_error(
                    ContextPreparationError(f"{type(exc).__name__}: {exc}"),
                    AgentStopReason.CONTEXT_ERROR,
                    step=step,
                )

            current_summary_state = context_decision.summary_state
            usage = _add_usage(usage, context_decision.summary_usage)
            request_messages = context_decision.messages
            request_tools = context_decision.tools
            await emitter.emit(
                AgentEventType.MODEL_STARTED,
                step=step,
                provider=resolved_provider,
                model=resolved_model,
                original_estimated_input_tokens=(
                    context_decision.original_estimated_input_tokens
                ),
                prepared_input_tokens=context_decision.prepared_input_tokens,
                estimated_input_tokens=context_decision.estimated_input_tokens,
                context_trimmed=context_decision.trimmed,
                context_window=context_decision.context_window,
                input_budget=context_decision.input_budget,
                usage_ratio=context_decision.usage_ratio,
                trigger_tokens=context_decision.trigger_tokens,
                target_tokens=context_decision.target_tokens,
                requires_compaction=context_decision.requires_compaction,
                exceeds_input_budget=context_decision.exceeds_input_budget,
                capability_source=context_decision.capability_source,
                original_usage_ratio=context_decision.original_usage_ratio,
                prepared_usage_ratio=context_decision.prepared_usage_ratio,
                compaction_stage=context_decision.compaction_stage.value,
                compacted_tool_results=context_decision.compacted_tool_results,
                removed_tool_rounds=context_decision.removed_tool_rounds,
                reached_target=context_decision.reached_target,
                needs_next_compaction_stage=(
                    context_decision.needs_next_compaction_stage
                ),
                summary_updated=context_decision.summary_updated,
                summarized_conversation_blocks=(
                    context_decision.summarized_conversation_blocks
                ),
                summary_usage=context_decision.summary_usage,
                summary_error=context_decision.summary_error,
            )
            if context_decision.exceeds_input_budget:
                return await stop_with_error(
                    ContextWindowExceededError(
                        context_decision.estimated_input_tokens or 0,
                        context_decision.input_budget or 0,
                    ),
                    AgentStopReason.CONTEXT_ERROR,
                    step=step,
                )

            try:
                response = await adapter.complete(
                    ModelRequest(
                        messages=request_messages,
                        model=resolved_model,
                        tools=request_tools,
                        max_output_tokens=effective_max_output_tokens,
                    )
                )
            except Exception as exc:
                return await stop_with_error(
                    ModelInvocationError(f"{type(exc).__name__}: {exc}"),
                    AgentStopReason.MODEL_ERROR,
                    step=step,
                )

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
                return self._result(
                    run_id=run_id,
                    final_message=assistant_message,
                    messages=messages,
                    steps=step,
                    stop_reason=AgentStopReason.FINAL_ANSWER,
                    tool_rounds=tool_rounds,
                    tool_calls=tool_calls,
                    usage=usage,
                    summary_state=current_summary_state,
                )

            round_records: list[ToolCallRecord] = []
            if self._checkpoint_store is not None:
                await self._checkpoint_store.before_tools(
                    run_id,
                    step=step,
                    tool_calls=tool_calls_in_message,
                )
            for tool_call in tool_calls_in_message:
                signature = self._tool_call_signature(tool_call)
                if signature == previous_signature:
                    repeated_count += 1
                else:
                    previous_signature = signature
                    repeated_count = 1

                if repeated_count >= 3:
                    error = RepeatedToolCallError(tool_call.name)
                    return self._result(
                        run_id=run_id,
                        final_message=self._error_message(error),
                        messages=messages,
                        steps=step,
                        stop_reason=AgentStopReason.REPEATED_TOOL_CALL,
                        tool_rounds=tool_rounds,
                        tool_calls=tool_calls,
                        usage=usage,
                        error=error,
                        summary_state=current_summary_state,
                    )

                result = await self._execute_tool(
                    tool_call,
                    context=ToolExecutionContext(
                        run_id=run_id,
                        conversation_id=conversation_id,
                        user_input=user_input,
                        step=step,
                        tool_call=tool_call,
                    ),
                    hook=tool_event_hook,
                )
                if self._checkpoint_store is not None:
                    await self._checkpoint_store.complete_tool(run_id, result)
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
        return self._result(
            run_id=run_id,
            final_message=self._error_message(error),
            messages=messages,
            steps=self._max_steps,
            stop_reason=AgentStopReason.MAX_STEPS,
            tool_rounds=tool_rounds,
            tool_calls=tool_calls,
            usage=usage,
            error=error,
            summary_state=current_summary_state,
        )

    async def _run_post_run_memory_reflection(
        self,
        result: AgentResult,
        *,
        user_input: str,
        conversation_id: str | None,
        emitter: _EventEmitter,
    ) -> None:
        """正常完成后同步反思普通 Memory；失败不改变主 AgentResult。"""

        reflector = self._memory_reflector
        if reflector is None:
            return
        if not reflector.enabled:
            await emitter.emit(
                AgentEventType.MEMORY_REFLECTION_SKIPPED,
                reflection_triggered=False,
                reflection_skip_reason="disabled",
            )
            return
        if result.stop_reason is not AgentStopReason.FINAL_ANSWER:
            await emitter.emit(
                AgentEventType.MEMORY_REFLECTION_SKIPPED,
                reflection_triggered=False,
                reflection_skip_reason=f"stop_reason={result.stop_reason.value}",
            )
            return
        await emitter.emit(
            AgentEventType.MEMORY_REFLECTION_STARTED,
            reflection_triggered=True,
            provider=reflector.provider_hint,
            model=reflector.model_hint,
        )
        try:
            if self._memory_manager is None:
                raise RuntimeError("memory manager unavailable")
            core_memory, memory_index = (
                await self._memory_manager.reflection_context()
            )
            task_context = ""
            if self._task_context_provider is not None:
                task_message = await self._task_context_provider.message_for(
                    conversation_id
                )
                if task_message is not None:
                    task_context = task_message.content or ""
            reflection_input = MemoryReflectionInput(
                run_id=result.run_id,
                conversation_id=conversation_id,
                user_input=user_input[:8_000],
                final_answer=(result.final_message.content or "")[:12_000],
                tool_context=_reflection_tool_context(
                    result.tool_calls,
                    max_chars=reflector.config.max_tool_context_chars,
                ),
                recalled_memory_ids=_recalled_memory_ids(result.tool_calls),
                core_memory=core_memory,
                memory_index=memory_index,
                task_context=task_context,
            )
            outcome = await reflector.run(reflection_input)
        except Exception as exc:
            await emitter.emit(
                AgentEventType.MEMORY_REFLECTION_FAILED,
                reflection_triggered=True,
                provider=reflector.provider_hint,
                model=reflector.model_hint,
                reflection_error=f"{type(exc).__name__}: {exc}",
            )
            return
        payload = {
            "reflection_triggered": outcome.triggered,
            "reflection_action": (
                outcome.action.value if outcome.action is not None else None
            ),
            "reflection_duration_ms": outcome.duration_ms,
            "reflection_error": outcome.error,
            "reflection_memory_id": outcome.memory_id,
            "reflection_maintenance_required": outcome.maintenance_required,
            "reflection_retention_candidate_ids": (
                outcome.retention_candidate_ids
            ),
            "provider": outcome.provider,
            "model": outcome.model,
            "usage": outcome.usage,
        }
        await emitter.emit(
            (
                AgentEventType.MEMORY_REFLECTION_FAILED
                if outcome.error is not None
                else AgentEventType.MEMORY_REFLECTION_COMPLETED
            ),
            **payload,
        )

    async def run_stream(
        self,
        user_input: str,
        *,
        history: Sequence[Message] = (),
        conversation_id: str | None = None,
        event_handler: AgentEventHandler | None = None,
        summary_state: ConversationSummaryState | None = None,
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
                    summary_state=summary_state,
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
        context: ToolExecutionContext,
        hook: ToolHook,
    ) -> ToolResult:
        try:
            return await self._tool_executor.execute(
                tool_call,
                context=context,
                hooks=(hook,),
            )
        except Exception as exc:
            result = ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=0.0,
            )
            await hook.after_execute(context, result)
            return result

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
        summary_state: ConversationSummaryState | None = None,
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
            summary_state=summary_state,
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


def _reflection_tool_context(
    records: Sequence[ToolCallRecord],
    *,
    max_chars: int,
) -> tuple[str, ...]:
    """为 Reflector 提供有界工具摘要，不默认复制全部原始输出。"""

    if max_chars <= 0:
        return ()
    remaining = max_chars
    items: list[str] = []
    for record in records:
        payload = json.dumps(
            {
                "tool": record.tool_call.name,
                "arguments": record.tool_call.arguments,
                "success": record.result.success,
                "output": record.result.output,
                "error": record.result.error,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        if len(payload) > remaining:
            payload = payload[:remaining]
        if payload:
            items.append(payload)
            remaining -= len(payload)
        if remaining <= 0:
            break
    return tuple(items)


def _recalled_memory_ids(records: Sequence[ToolCallRecord]) -> tuple[str, ...]:
    """返回本轮确实成功读到完整正文的普通 Memory ID。"""

    recalled: list[str] = []
    for record in records:
        if record.tool_call.name != "memory.read" or not record.result.success:
            continue
        arguments: Any = record.tool_call.arguments
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                continue
        if not isinstance(arguments, dict):
            continue
        memory_id = arguments.get("memory_id")
        if not isinstance(memory_id, str):
            continue
        try:
            output = json.loads(record.result.output or "")
        except (json.JSONDecodeError, TypeError):
            continue
        normalized = memory_id.strip().upper()
        if (
            isinstance(output, dict)
            and output.get("found") is True
            and output.get("id") == normalized
            and normalized not in recalled
        ):
            recalled.append(normalized)
    return tuple(recalled)


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
