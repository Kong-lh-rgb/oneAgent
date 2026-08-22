"""最小模型与工具执行循环。"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import suppress
from typing import Any
from uuid import uuid4

from app.checkpoint import (
    RunCheckpoint,
    SQLiteCheckpointStore,
    render_checkpoint_context,
)
from app.context import ContextManager, ConversationSummaryState
from app.memory import (
    MaintenanceAction,
    MemoryMaintenanceCandidate,
    MemoryMaintenanceInput,
    MemoryMaintenanceReflector,
    MemoryManager,
    MemoryReflectionInput,
    PostRunMemoryReflector,
    ReflectionAction,
    decide_reflection_gate,
)
from app.models.registry import ModelAdapterRegistry
from app.models.types import (
    AgentMode,
    Message,
    MessageRole,
    ModelProvider,
    ModelRequest,
    ModelUsage,
    ToolCall,
    ToolResult,
    add_model_usage,
)
from app.skills import (
    SKILL_READ_TOOL_NAME,
    Skill,
    SkillContextProvider,
    SkillMetadata,
    SkillStore,
)
from app.task.context import TaskContextProvider
from app.tools.approval import ApprovalGate
from app.tools.catalog import (
    TOOL_SEARCH_NAME,
    activated_tool_names,
    ensure_tool_search_registered,
)
from app.tools.executor import ToolExecutor
from app.tools.hooks import ToolExecutionContext, ToolHook
from app.tools.observability import ToolExecutionRecord
from app.tools.permissions.policy import PermissionPolicyEngine
from app.tools.permissions.store import PermissionRuleStore
from app.tools.registry import ToolRegistry

from .budget import (
    RunBudget,
    RunBudgetConfig,
    RunBudgetDecision,
    RunBudgetStatus,
    chargeable_tokens,
)
from .computer_guard import ComputerStagnationGuard
from .errors import (
    AgentRuntimeError,
    ContextPreparationError,
    ContextWindowExceededError,
    MaxStepsExceededError,
    ModelInvocationError,
    RepeatedToolCallError,
    RunBudgetExceededError,
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

_LEGACY_DATE_PATTERN = re.compile(r"当前日期是 \d{4}-\d{2}-\d{2}。")

# Plan Mode 系统指令（补充；真正的限制由工具过滤 + 执行层硬阻断保证）。
_PLAN_MODE_SYSTEM_MESSAGE = (
    "你现在处于 PLAN MODE（规划模式）：只分析、调查并形成计划，不要修改用户环境。\n"
    "你可以使用只读 / 搜索工具（read_file、list_files、web_search、current_time、"
    "memory_read）与任务工具（task_create、task_update、task_get、task_list）。\n"
    "完成必要调查后，必须创建（task_create）或更新（task_update）一个 PENDING 任务"
    "作为本轮计划，至少包含 title、goal 与具体可执行的 steps；不要伪造 DONE 步骤、"
    "已完成 state 或已验证 key_facts。\n"
    "最后返回简洁的计划说明。"
)

_PLAN_NO_TASK_MESSAGE = "Plan mode finished without creating a task."
_PLAN_NO_VALID_TASK_MESSAGE = "Plan mode finished without a valid pending task."
_RUN_BUDGET_FINALIZATION_MESSAGE = (
    "本 Run 已达到 Main Agent 用量收口线。不要再调用工具，请基于已有证据立即"
    "给出简洁的最终答复；明确区分已完成、未完成和无法验证的内容，不要伪造"
    "执行结果。"
)
_RUN_BUDGET_WARNING_MESSAGE = (
    "本 Run 的累计 Main Agent 用量已进入预警区。请减少不必要的重复调查和工具"
    "调用，优先完成当前目标；仍可在确有必要时继续使用工具。"
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
        max_tool_rounds: int | None = None,
        max_output_tokens: int | None = None,
        tool_executor: ToolExecutor | None = None,
        tool_hooks: Sequence[ToolHook] = (),
        approval_gate: ApprovalGate | None = None,
        policy_engine: PermissionPolicyEngine | None = None,
        rule_store: PermissionRuleStore | None = None,
        context_manager: ContextManager | None = None,
        task_context_provider: TaskContextProvider | None = None,
        checkpoint_store: SQLiteCheckpointStore | None = None,
        memory_manager: MemoryManager | None = None,
        memory_reflector: PostRunMemoryReflector | None = None,
        memory_maintenance_reflector: MemoryMaintenanceReflector | None = None,
        skill_store: SkillStore | None = None,
        skill_context_provider: SkillContextProvider | None = None,
        post_run_submit: Callable[[Callable[[], Any]], bool] | None = None,
        run_budget_config: RunBudgetConfig | None = None,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        if max_output_tokens is not None and max_output_tokens < 1:
            raise ValueError("max_output_tokens must be at least 1")
        if max_tool_rounds is not None and max_tool_rounds < 1:
            raise ValueError("max_tool_rounds must be at least 1")
        if memory_reflector is not None and memory_manager is None:
            raise ValueError("memory_reflector requires memory_manager")
        if memory_maintenance_reflector is not None and memory_manager is None:
            raise ValueError("memory_maintenance_reflector requires memory_manager")
        if skill_context_provider is not None and skill_store is None:
            raise ValueError("skill_context_provider requires skill_store")

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
        self._memory_maintenance_reflector = memory_maintenance_reflector
        self._skill_store = skill_store
        self._skill_context_provider = skill_context_provider
        self._post_run_submit = post_run_submit
        self._run_budget = RunBudget(run_budget_config)
        self._tool_executor = tool_executor or ToolExecutor(
            tool_registry,
            approval_gate=approval_gate,
            policy_engine=policy_engine,
            rule_store=rule_store,
            hooks=tool_hooks,
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
        run_id: str | None = None,
        recovery_run_id: str | None = None,
        mode: AgentMode = AgentMode.NORMAL,
    ) -> AgentResult:
        """处理一次用户输入并返回完整的运行结果（AgentResult）。

        模型和工具运行时错误不会向外抛出，而是以结构化的
        ``AgentResult.error`` 与停止原因返回。

        ``run_id`` 可选：由调用方指定 Run ID（RunManager 用它把 Run 与
        Checkpoint / Trace 关联），缺省时内部生成。
        ``recovery_run_id`` 可选：显式指定要恢复的旧中断 Checkpoint（对应旧 Run）。
        只有显式传入 ``recovery_run_id`` 时本 Run 才会加载恢复证据；普通调用
        永远不隐式自动恢复 —— 恢复哪个 Run 的决定权属于 RunManager.recover()。
        ``mode`` 可选：一次执行的模式（NORMAL / PLAN），默认 NORMAL。
        """

        run_id = run_id or uuid4().hex
        emitter = _EventEmitter(
            handler=event_handler or NullEventHandler(),
            run_id=run_id,
            conversation_id=conversation_id,
        )
        try:
            recovery_checkpoint: RunCheckpoint | None = None
            if self._checkpoint_store is not None:
                if recovery_run_id is not None:
                    recovery_checkpoint = (
                        await self._checkpoint_store.get_unrecovered(
                            recovery_run_id
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
                    mode=mode,
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
            # Critical path 在 checkpoint + AGENT_COMPLETED/FAILED 处结束；
            # Memory Reflection 属于 post-run housekeeping，移交给后台（见
            # _schedule_post_run），不再阻塞 Run completed / conversation.send。
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
            await self._schedule_post_run(
                result,
                user_input=user_input,
                conversation_id=conversation_id,
                emitter=emitter,
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
        mode: AgentMode,
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
        main_model_calls = 0
        budget_chargeable_tokens = 0
        current_summary_state = summary_state
        memory_context_messages: tuple[Message, ...] = ()
        memory_context_loaded = False
        active_skills: dict[str, Skill] = {}
        skill_catalog_loaded = False
        catalog_metadata: tuple[SkillMetadata, ...] = ()
        activated_tools: set[str] = set()
        ensure_tool_search_registered(self._tool_registry)
        # Plan Mode：本轮是否创建/更新了 PENDING Task，以及最新 Task ID。
        plan_task_created = False
        plan_task_id: str | None = None
        finalization_pending = False
        computer_verification_pending = False
        computer_guard = ComputerStagnationGuard()
        computer_halted = False
        budget_warning_emitted = False
        budget_finalization_attempted = False

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

        for step in range(1, self._max_steps + 2):
            finalization_step = step > self._max_steps
            if finalization_step and not finalization_pending:
                break
            budget_decision = self._run_budget.evaluate(
                usage,
                chargeable_tokens_override=budget_chargeable_tokens,
                model_calls_override=main_model_calls,
            )
            budget_warning_in_request = budget_decision.should_warn
            budget_config = self._run_budget.config
            if budget_decision.exceeded:
                await emitter.emit(
                    AgentEventType.RUN_BUDGET_EXCEEDED,
                    step=step,
                    **_run_budget_event_fields(budget_decision, budget_config),
                )
                return await stop_with_error(
                    RunBudgetExceededError(
                        _run_budget_detail(budget_decision)
                    ),
                    AgentStopReason.RUN_BUDGET,
                    step=max(0, step - 1),
                )
            if budget_decision.should_warn and not budget_warning_emitted:
                budget_warning_emitted = True
                await emitter.emit(
                    AgentEventType.RUN_BUDGET_WARNING,
                    step=step,
                    **_run_budget_event_fields(budget_decision, budget_config),
                )
            budget_forces_final = budget_decision.should_finalize
            if budget_forces_final:
                if budget_finalization_attempted:
                    await emitter.emit(
                        AgentEventType.RUN_BUDGET_EXCEEDED,
                        step=step,
                        **_run_budget_event_fields(
                            budget_decision,
                            budget_config,
                            status=RunBudgetStatus.EXCEEDED,
                        ),
                    )
                    return await stop_with_error(
                        RunBudgetExceededError(
                            "dedicated finalization call was already used"
                        ),
                        AgentStopReason.RUN_BUDGET,
                        step=max(0, step - 1),
                    )
                budget_finalization_attempted = True
                await emitter.emit(
                    AgentEventType.RUN_BUDGET_FINALIZING,
                    step=step,
                    **_run_budget_event_fields(budget_decision, budget_config),
                )
            if self._checkpoint_store is not None:
                await self._checkpoint_store.before_model(run_id, step=step)
            force_final_answer = (
                finalization_step
                or computer_halted
                or budget_forces_final
                or (
                    self._max_tool_rounds is not None
                    and len(tool_rounds) >= self._max_tool_rounds
                )
            )
            # 原始历史保持不变；模型请求视图会移除旧版持久提示词中的固定日期。
            request_messages = tuple(
                _without_legacy_fixed_date(message) for message in messages
            )
            request_tools = (
                ()
                if force_final_answer
                else self._tool_registry.model_definitions_for_mode(
                    mode,
                    activated_names=activated_tools,
                )
            )
            # 先解析实际使用的模型和输出上限，确保预算与请求完全一致。
            try:
                adapter = self._model_registry.get(self._provider)
                resolved_model = self._model or adapter.default_model
                resolved_provider = adapter.provider
                effective_max_output_tokens = (
                    self._max_output_tokens or adapter.config.default_max_output_tokens
                )
                if budget_forces_final:
                    effective_max_output_tokens = min(
                        effective_max_output_tokens,
                        budget_config.finalization_max_output_tokens,
                    )
            except Exception as exc:
                return await stop_with_error(
                    ModelInvocationError(f"{type(exc).__name__}: {exc}"),
                    AgentStopReason.MODEL_ERROR,
                    step=step,
                )

            try:
                # 易变事实通过按需工具获取；这里仅组装确需常驻的临时上下文。
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
                if (
                    self._skill_context_provider is not None
                    and self._skill_store is not None
                ):
                    if not skill_catalog_loaded:
                        skill_catalog_loaded = True
                        try:
                            catalog_metadata = await self._skill_store.catalog()
                        except Exception:
                            catalog_metadata = ()
                    catalog_message = (
                        self._skill_context_provider.catalog_message(
                            catalog_metadata
                        )
                    )
                    if catalog_message is not None:
                        ephemeral_messages.append(catalog_message)
                injected_active_skill_names: tuple[str, ...] = ()
                if self._skill_context_provider is not None and active_skills:
                    active_messages = (
                        self._skill_context_provider.active_messages(
                            tuple(active_skills.values())
                        )
                    )
                    if active_messages:
                        injected_active_skill_names = tuple(active_skills)
                        ephemeral_messages.extend(active_messages)
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
                if mode is AgentMode.PLAN:
                    ephemeral_messages.append(
                        Message(
                            role=MessageRole.SYSTEM,
                            name="vesta_plan_mode",
                            content=_PLAN_MODE_SYSTEM_MESSAGE,
                        )
                    )
                if budget_warning_in_request:
                    ephemeral_messages.append(
                        Message(
                            role=MessageRole.SYSTEM,
                            content=_RUN_BUDGET_WARNING_MESSAGE,
                        )
                    )
                if ephemeral_messages:
                    request_messages = (
                        *request_messages[:historical_message_count],
                        *ephemeral_messages,
                        *request_messages[historical_message_count:],
                    )
                if force_final_answer:
                    if computer_halted:
                        final_instruction = (
                            "Computer 操作已因同一失败且桌面无进展而停止。不要再输出"
                            "或伪造任何工具调用；请根据已有证据直接说明阻塞原因、"
                            "已经完成的部分和用户可采取的恢复步骤。"
                        )
                    elif budget_forces_final:
                        final_instruction = _RUN_BUDGET_FINALIZATION_MESSAGE
                    else:
                        final_instruction = (
                            "工具调用轮次已用完。请读取最后一条工具结果，停止"
                            "调用工具并直接回答用户。对于 verification_status="
                            "unverified 的电脑操作，只能说明事件已投递、效果未"
                            "确认，不能宣称界面操作已经完成。"
                        )
                    request_messages = (
                        *request_messages,
                        Message(
                            role=MessageRole.SYSTEM,
                            content=final_instruction,
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
            main_model_calls += _usage_call_count(context_decision.summary_usage)
            budget_chargeable_tokens += chargeable_tokens(
                context_decision.summary_usage
            )
            request_messages = context_decision.messages
            request_tools = context_decision.tools
            # Context summary 也是 critical-path 模型调用；它可能让本 Run 在真正
            # 请求主模型前跨过预算线，因此准备完成后必须再评估一次。
            prepared_budget_decision = self._run_budget.evaluate(
                usage,
                chargeable_tokens_override=budget_chargeable_tokens,
                model_calls_override=main_model_calls,
            )
            budget_decision = prepared_budget_decision
            if budget_decision.exceeded:
                await emitter.emit(
                    AgentEventType.RUN_BUDGET_EXCEEDED,
                    step=step,
                    **_run_budget_event_fields(budget_decision, budget_config),
                )
                return await stop_with_error(
                    RunBudgetExceededError(_run_budget_detail(budget_decision)),
                    AgentStopReason.RUN_BUDGET,
                    step=max(0, step - 1),
                )
            if budget_decision.should_warn and not budget_warning_emitted:
                budget_warning_emitted = True
                await emitter.emit(
                    AgentEventType.RUN_BUDGET_WARNING,
                    step=step,
                    **_run_budget_event_fields(budget_decision, budget_config),
                )
            if budget_decision.should_warn and not budget_warning_in_request:
                request_messages = (
                    *request_messages,
                    Message(
                        role=MessageRole.SYSTEM,
                        content=_RUN_BUDGET_WARNING_MESSAGE,
                    ),
                )
            if budget_decision.should_finalize and not budget_forces_final:
                budget_forces_final = True
                force_final_answer = True
                budget_finalization_attempted = True
                request_tools = ()
                effective_max_output_tokens = min(
                    effective_max_output_tokens,
                    budget_config.finalization_max_output_tokens,
                )
                request_messages = (
                    *request_messages,
                    Message(
                        role=MessageRole.SYSTEM,
                        content=_RUN_BUDGET_FINALIZATION_MESSAGE,
                    ),
                )
                await emitter.emit(
                    AgentEventType.RUN_BUDGET_FINALIZING,
                    step=step,
                    **_run_budget_event_fields(budget_decision, budget_config),
                )
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
                working_input_budget=context_decision.working_input_budget,
                hard_trigger_tokens=context_decision.hard_trigger_tokens,
                hard_target_tokens=context_decision.hard_target_tokens,
                usage_ratio=context_decision.usage_ratio,
                trigger_tokens=context_decision.trigger_tokens,
                target_tokens=context_decision.target_tokens,
                tool_result_budget_tokens=(
                    context_decision.tool_result_budget_tokens
                ),
                tool_result_tokens_before=(
                    context_decision.tool_result_tokens_before
                ),
                tool_result_tokens_after=(
                    context_decision.tool_result_tokens_after
                ),
                tool_schema_tokens=context_decision.tool_schema_tokens,
                message_tokens_before=context_decision.message_tokens_before,
                message_tokens_after=context_decision.message_tokens_after,
                unsummarized_conversation_blocks=(
                    context_decision.unsummarized_conversation_blocks
                ),
                conversation_block_limit=(
                    context_decision.conversation_block_limit
                ),
                conversation_block_triggered=(
                    context_decision.conversation_block_triggered
                ),
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
                available_skill_count=(
                    len(catalog_metadata)
                    if self._skill_context_provider is not None
                    else None
                ),
                skill_catalog_tokens=(
                    self._skill_context_provider.catalog_tokens(
                        catalog_metadata
                    )
                    if self._skill_context_provider is not None
                    else None
                ),
                active_skill_names=tuple(active_skills),
                active_skill_tokens=(
                    self._skill_context_provider.active_tokens(
                        tuple(active_skills.values())
                    )
                    if self._skill_context_provider is not None
                    else None
                ),
                active_skill_message_names=injected_active_skill_names,
                **_run_budget_event_fields(budget_decision, budget_config),
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
                async def emit_text_delta(delta: str) -> None:
                    if not delta:
                        return
                    await emitter.emit(
                        AgentEventType.MODEL_OUTPUT_DELTA,
                        step=step,
                        provider=resolved_provider,
                        model=resolved_model,
                        delta=delta,
                    )

                async def emit_reasoning_delta(delta: str) -> None:
                    if not delta:
                        return
                    await emitter.emit(
                        AgentEventType.MODEL_REASONING_DELTA,
                        step=step,
                        provider=resolved_provider,
                        model=resolved_model,
                        reasoning_delta=delta,
                    )

                response = await adapter.complete_stream(
                    ModelRequest(
                        messages=request_messages,
                        model=resolved_model,
                        tools=request_tools,
                        max_output_tokens=effective_max_output_tokens,
                    ),
                    on_text_delta=emit_text_delta,
                    on_reasoning_delta=emit_reasoning_delta,
                )
            except Exception as exc:
                return await stop_with_error(
                    ModelInvocationError(f"{type(exc).__name__}: {exc}"),
                    AgentStopReason.MODEL_ERROR,
                    step=step,
                )

            usage = _add_usage(usage, response.usage)
            main_model_calls += max(1, response.usage.model_calls)
            budget_chargeable_tokens += chargeable_tokens(response.usage)
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
            if force_final_answer and tool_calls_in_message:
                # 最终化请求没有提供工具定义；即使模型伪造工具调用也不执行。
                if budget_forces_final:
                    return await stop_with_error(
                        RunBudgetExceededError(
                            "model attempted a tool call during budget finalization"
                        ),
                        AgentStopReason.RUN_BUDGET,
                        step=step,
                    )
                break
            if not tool_calls_in_message:
                if force_final_answer and _looks_like_textual_tool_call(
                    assistant_message.content
                ):
                    # 部分模型在 tools=() 时仍会把 Provider 工具协议作为普通文本
                    # 输出。它没有被执行，不能因此把 Run 标为 completed。
                    if budget_forces_final:
                        return await stop_with_error(
                            RunBudgetExceededError(
                                "model emitted a textual tool call during budget "
                                "finalization"
                            ),
                            AgentStopReason.RUN_BUDGET,
                            step=step,
                        )
                    break
                if computer_verification_pending and not computer_halted:
                    messages.append(
                        Message(
                            role=MessageRole.SYSTEM,
                            content=(
                                "最近一次 computer_type 只确认输入事件已投递，尚未"
                                "确认界面效果。必须先调用 computer_observe 获取新证据，"
                                "不能直接向用户宣称操作完成。"
                            ),
                        )
                    )
                    continue
                final_message = assistant_message
                if mode is AgentMode.PLAN:
                    # Plan Mode 完成条件：不仅要 task_create/task_update 成功，
                    # 还要最终重新读取 Task 并确认是有效的 PENDING 计划
                    # （PENDING + goal 非空 + steps 非空 + 无 DONE/IN_PROGRESS 步骤）。
                    plan_valid = False
                    if (
                        plan_task_id is not None
                        and self._task_context_provider is not None
                    ):
                        plan_valid = (
                            await self._task_context_provider.pending_plan_is_valid(
                                conversation_id,
                                plan_task_id,
                            )
                        )
                    if not plan_valid:
                        prefix = (
                            _PLAN_NO_TASK_MESSAGE
                            if not plan_task_created
                            else _PLAN_NO_VALID_TASK_MESSAGE
                        )
                        final_message = _plan_failure_message(
                            assistant_message,
                            prefix,
                        )
                        plan_task_id = None
                        messages[-1] = final_message
                return self._result(
                    run_id=run_id,
                    final_message=final_message,
                    messages=messages,
                    steps=step,
                    stop_reason=AgentStopReason.FINAL_ANSWER,
                    tool_rounds=tool_rounds,
                    tool_calls=tool_calls,
                    usage=usage,
                    plan_task_id=plan_task_id,
                    summary_state=current_summary_state,
                )

            round_records: list[ToolCallRecord] = []
            pending_activations: set[str] = set()
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

                execution_context = ToolExecutionContext(
                    run_id=run_id,
                    conversation_id=conversation_id,
                    user_input=user_input,
                    step=step,
                    tool_call=tool_call,
                    metadata={"active_skill_names": tuple(active_skills)},
                    mode=mode,
                )
                if computer_halted and tool_call.name.startswith("computer_"):
                    await tool_event_hook.before_execute(execution_context)
                    result = ToolResult(
                        tool_call_id=tool_call.id,
                        tool_name=tool_call.name,
                        success=False,
                        error=(
                            "computer_attempts_halted: repeated failures without "
                            "desktop progress; explain the blocker instead"
                        ),
                        duration_ms=0,
                    )
                    await tool_event_hook.after_execute(execution_context, result)
                elif (
                    mode is AgentMode.PLAN
                    and not self._tool_registry.is_allowed_for_mode(
                        tool_call.name,
                        mode,
                    )
                ):
                    # Plan Mode 能力过滤：硬性阻断副作用工具（即使模型伪造调用）。
                    await tool_event_hook.before_execute(execution_context)
                    result = ToolResult(
                        tool_call_id=tool_call.id,
                        tool_name=tool_call.name,
                        success=False,
                        error=(
                            "Tool is not allowed in plan mode "
                            "(read-only / planning tools only)."
                        ),
                        duration_ms=0,
                    )
                    await tool_event_hook.after_execute(execution_context, result)
                elif (
                    self._tool_registry.is_deferred(tool_call.name)
                    and tool_call.name not in activated_tools
                ):
                    await tool_event_hook.before_execute(execution_context)
                    result = ToolResult(
                        tool_call_id=tool_call.id,
                        tool_name=tool_call.name,
                        success=False,
                        error=(
                            "Deferred tool is not active. Call tool_search first."
                        ),
                        duration_ms=0,
                    )
                    await tool_event_hook.after_execute(execution_context, result)
                else:
                    result = await self._execute_tool(
                        tool_call,
                        context=execution_context,
                        hook=tool_event_hook,
                    )
                guard_decision = computer_guard.record(tool_call, result)
                if guard_decision.feedback:
                    result = result.model_copy(
                        update={
                            "error": "\n".join(
                                part
                                for part in (
                                    result.error,
                                    guard_decision.feedback,
                                )
                                if part
                            )
                        }
                    )
                if guard_decision.halt:
                    computer_halted = True
                if self._checkpoint_store is not None:
                    await self._checkpoint_store.complete_tool(run_id, result)
                if (
                    mode is AgentMode.PLAN
                    and result.success
                    and tool_call.name in ("task_create", "task_update")
                ):
                    # Plan Mode 完成条件：成功创建 / 更新了 PENDING Task。
                    plan_task_created = True
                    task_id = _plan_task_id_from_output(result.output)
                    if task_id:
                        plan_task_id = task_id
                record = ToolCallRecord(
                    round_index=len(tool_rounds),
                    tool_call=tool_call,
                    result=result,
                )
                round_records.append(record)
                tool_calls.append(record)
                messages.append(self._tool_result_message(result))
                if tool_call.name == "computer_type" and result.success:
                    computer_verification_pending = (
                        _computer_verification_status(result.output)
                        == "unverified"
                    )
                elif (
                    tool_call.name == "computer_observe"
                    and result.success
                    and computer_verification_pending
                ):
                    computer_verification_pending = False
                if tool_call.name == TOOL_SEARCH_NAME and result.success:
                    pending_activations.update(
                        name
                        for name in activated_tool_names(result.output)
                        if self._tool_registry.is_deferred(name)
                    )
                if (
                    tool_call.name == SKILL_READ_TOOL_NAME
                    and result.success
                ):
                    await self._activate_skill_from_tool_result(
                        result,
                        active_skills,
                        emitter=emitter,
                        step=step,
                    )

            tool_rounds.append(
                ToolRound(
                    round_index=len(tool_rounds),
                    assistant_message=assistant_message,
                    records=tuple(round_records),
                )
            )
            # 本轮模型没有见过新定义，必须等下一步请求后才能调用。
            activated_tools.update(pending_activations)
            finalization_pending = (
                step == self._max_steps
                and (
                    computer_halted
                    or any(
                        call.name == "computer_observe"
                        for call in tool_calls_in_message
                    )
                    or self._run_budget.evaluate(
                        usage,
                        chargeable_tokens_override=budget_chargeable_tokens,
                        model_calls_override=main_model_calls,
                    ).should_finalize
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

    async def _activate_skill_from_tool_result(
        self,
        result: ToolResult,
        active_skills: dict[str, Skill],
        *,
        emitter: _EventEmitter,
        step: int,
    ) -> None:
        """尝试把 skill_read 命中的 skill 加入本 run 的 active set（受预算约束）。"""
        if self._skill_store is None or self._skill_context_provider is None:
            return
        skill_name = _skill_read_activated_name(result.output)
        if not skill_name or skill_name in active_skills:
            return
        skill = await self._skill_store.load(skill_name)
        if skill is None:
            await emitter.emit(
                AgentEventType.SKILL_ACTIVATION_FAILED,
                step=step,
                skill_name=skill_name,
                skill_error="skill not found",
            )
            return
        if self._skill_context_provider.would_exceed_budget(
            tuple(active_skills.values()),
            skill,
        ):
            await emitter.emit(
                AgentEventType.SKILL_ACTIVATION_FAILED,
                step=step,
                skill_name=skill_name,
                skill_error="active skill context budget exceeded",
            )
            return
        active_skills[skill.metadata.name] = skill
        await emitter.emit(
            AgentEventType.SKILL_ACTIVATED,
            step=step,
            skill_name=skill.metadata.name,
            skill_scope=skill.metadata.scope.value,
            active_skill_names=tuple(active_skills),
            active_skill_tokens=self._skill_context_provider.active_tokens(
                tuple(active_skills.values())
            ),
        )

    async def _schedule_post_run(
        self,
        result: AgentResult,
        *,
        user_input: str,
        conversation_id: str | None,
        emitter: _EventEmitter,
    ) -> None:
        """Post-run housekeeping 调度（critical path 结束点调用）。

        在 Run 完成（AGENT_COMPLETED/FAILED 已发）后判定是否需要 Memory
        Reflection，构造稳定 snapshot 输入，并交给后台执行；没有后台
        processor（如直接构造 Runtime 的测试）时同步 fallback 保持旧行为。
        失败不改变主结果。
        """

        reflector = self._memory_reflector
        manager = self._memory_manager

        if reflector is None:
            if (
                self._memory_maintenance_reflector is not None
                and result.stop_reason is AgentStopReason.FINAL_ANSWER
            ):
                await self._ensure_memory_capacity(
                    required_slots=0,
                    emitter=emitter,
                )
            return
        if not reflector.enabled:
            await emitter.emit(
                AgentEventType.MEMORY_REFLECTION_SKIPPED,
                reflection_triggered=False,
                reflection_skip_reason="disabled",
            )
            if (
                self._memory_maintenance_reflector is not None
                and result.stop_reason is AgentStopReason.FINAL_ANSWER
            ):
                await self._ensure_memory_capacity(
                    required_slots=0,
                    emitter=emitter,
                )
            return
        if result.stop_reason is not AgentStopReason.FINAL_ANSWER:
            await emitter.emit(
                AgentEventType.MEMORY_REFLECTION_SKIPPED,
                reflection_triggered=False,
                reflection_skip_reason=f"stop_reason={result.stop_reason.value}",
            )
            return

        recalled_memory_revisions = _recalled_memory_revisions(result.tool_calls)
        if reflector.config.gate_enabled:
            gate = decide_reflection_gate(
                user_input,
                recalled_memory_ids=tuple(recalled_memory_revisions),
            )
            if not gate.should_reflect:
                await emitter.emit(
                    AgentEventType.MEMORY_REFLECTION_SKIPPED,
                    reflection_triggered=False,
                    reflection_skip_reason=f"gate:{gate.reason.value}",
                )
                if manager is not None:
                    await self._ensure_memory_capacity(
                        required_slots=0,
                        emitter=emitter,
                    )
                return

        # 构造稳定 snapshot：在 Run 完成时捕获输入，后台任务不再读取当前
        # conversation / memory，避免下一轮输入污染上一 Run 的 reflection。
        try:
            if manager is None:
                raise RuntimeError("memory manager unavailable")
            core_memory, memory_index = await manager.reflection_context()
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
                recalled_memory_ids=tuple(recalled_memory_revisions),
                core_memory=core_memory,
                memory_index=memory_index,
                task_context=task_context,
            )
        except Exception as exc:
            # snapshot 构造失败：不能把已完成的 Run 改成 failed；只记录 post-run 失败。
            await emitter.emit(
                AgentEventType.MEMORY_REFLECTION_FAILED,
                reflection_triggered=True,
                reflection_error=f"{type(exc).__name__}: {exc}",
                reflection_mutation_applied=False,
                provider=reflector.provider_hint,
                model=reflector.model_hint,
                usage=ModelUsage(),
            )
            if manager is not None:
                await self._ensure_memory_capacity(
                    required_slots=0,
                    emitter=emitter,
                )
            return

        async def _job() -> None:
            await self._run_post_run_reflection(
                reflector=reflector,
                manager=manager,
                reflection_input=reflection_input,
                recalled_memory_revisions=recalled_memory_revisions,
                emitter=emitter,
            )

        if self._post_run_submit is not None:
            self._post_run_submit(_job)
        else:
            # 无后台 processor（直接构造 Runtime）：同步 fallback，保持旧语义。
            await _job()

    async def _run_post_run_reflection(
        self,
        *,
        reflector: PostRunMemoryReflector,
        manager: MemoryManager,
        reflection_input: MemoryReflectionInput,
        recalled_memory_revisions: dict[str, int],
        emitter: _EventEmitter,
    ) -> None:
        """后台执行 Memory Reflection：决策 → 应用 → 维护。失败只进事件/日志。

        CREATE / UPDATE 的 revision 与 capacity 语义由 MemoryManager 保证，
        与下一 Run 并发也安全（本方法只做决策与应用，不读 conversation）。
        """

        await emitter.emit(
            AgentEventType.MEMORY_REFLECTION_STARTED,
            reflection_triggered=True,
            provider=reflector.provider_hint,
            model=reflector.model_hint,
        )
        proposal = None
        memory_id: str | None = None
        mutation_applied = False
        try:
            proposal = await reflector.decide(reflection_input)
            if proposal.error is not None:
                raise RuntimeError(proposal.error)
            if proposal.decision is None:
                raise RuntimeError("reflection model returned no decision")

            decision = proposal.decision
            if decision.action is ReflectionAction.UPDATE:
                memory_id = decision.memory_id
                expected_revision = recalled_memory_revisions.get(memory_id or "")
                if expected_revision is None:
                    raise ValueError(
                        "reflection update requires memory_read success in current run"
                    )
                record = await manager.update_if_revision(
                    memory_id or "",
                    expected_revision=expected_revision,
                    title=decision.title or "",
                    summary=decision.summary or "",
                    content=decision.content or "",
                    reason=decision.reason,
                )
                memory_id = record.id
                mutation_applied = True
            elif decision.action is ReflectionAction.CREATE:
                manager.validate_create(
                    title=decision.title or "",
                    summary=decision.summary or "",
                    content=decision.content or "",
                )
                capacity_ready = await self._ensure_memory_capacity(
                    required_slots=1,
                    emitter=emitter,
                )
                if not capacity_ready:
                    raise RuntimeError(
                        "memory create skipped because capacity is unavailable"
                    )
                record = await manager.create_if_capacity(
                    title=decision.title or "",
                    summary=decision.summary or "",
                    content=decision.content or "",
                )
                if record is None:
                    raise RuntimeError(
                        "memory create lost the available slot to a concurrent run"
                    )
                memory_id = record.id
                mutation_applied = True
        except Exception as exc:
            provider = (
                proposal.provider
                if proposal is not None
                else reflector.provider_hint
            )
            model = proposal.model if proposal is not None else reflector.model_hint
            usage = proposal.usage if proposal is not None else ModelUsage()
            duration_ms = proposal.duration_ms if proposal is not None else None
            await emitter.emit(
                AgentEventType.MEMORY_REFLECTION_FAILED,
                reflection_triggered=True,
                reflection_action=(
                    proposal.decision.action.value
                    if proposal is not None and proposal.decision is not None
                    else None
                ),
                reflection_duration_ms=duration_ms,
                reflection_error=f"{type(exc).__name__}: {exc}",
                reflection_memory_id=(
                    proposal.decision.memory_id
                    if proposal is not None and proposal.decision is not None
                    else None
                ),
                reflection_mutation_applied=False,
                reflection_input_json=(
                    proposal.input_json if proposal is not None else None
                ),
                reflection_raw_output=(
                    proposal.raw_output if proposal is not None else None
                ),
                provider=provider,
                model=model,
                usage=usage,
            )
            if (
                proposal is None
                or proposal.decision is None
                or proposal.decision.action is not ReflectionAction.CREATE
            ):
                await self._ensure_memory_capacity(
                    required_slots=0,
                    emitter=emitter,
                )
            return

        maintenance_required = await manager.maintenance_required()
        candidate_ids: tuple[str, ...] = ()
        if maintenance_required:
            candidates = await manager.retention_candidates()
            candidate_ids = tuple(item.id for item in candidates)
        await emitter.emit(
            AgentEventType.MEMORY_REFLECTION_COMPLETED,
            reflection_triggered=True,
            reflection_action=proposal.decision.action.value,
            reflection_duration_ms=proposal.duration_ms,
            reflection_memory_id=memory_id,
            reflection_mutation_applied=mutation_applied,
            reflection_maintenance_required=maintenance_required,
            reflection_retention_candidate_ids=candidate_ids,
            reflection_input_json=proposal.input_json,
            reflection_raw_output=proposal.raw_output,
            provider=proposal.provider,
            model=proposal.model,
            usage=proposal.usage,
        )
        if maintenance_required:
            await self._ensure_memory_capacity(
                required_slots=0,
                emitter=emitter,
            )

    async def _ensure_memory_capacity(
        self,
        *,
        required_slots: int,
        emitter: _EventEmitter,
    ) -> bool:
        """隔离整个容量协调路径，任何异常都不能改变主 AgentResult。"""

        try:
            return await self._ensure_memory_capacity_impl(
                required_slots=required_slots,
                emitter=emitter,
            )
        except Exception as exc:
            await emitter.emit(
                AgentEventType.MEMORY_MAINTENANCE_FAILED,
                maintenance_triggered=True,
                maintenance_error=f"{type(exc).__name__}: {exc}",
            )
            return False

    async def _ensure_memory_capacity_impl(
        self,
        *,
        required_slots: int,
        emitter: _EventEmitter,
    ) -> bool:
        """通过可恢复归档腾出容量；无法安全维护时返回 False。"""

        manager = self._memory_manager
        if manager is None:
            return False
        active_count = await manager.active_count()
        if active_count + required_slots <= manager.max_active:
            return True

        maintainer = self._memory_maintenance_reflector
        if maintainer is None or not maintainer.enabled:
            await emitter.emit(
                AgentEventType.MEMORY_MAINTENANCE_SKIPPED,
                maintenance_triggered=False,
                maintenance_skip_reason=(
                    "unavailable" if maintainer is None else "disabled"
                ),
                maintenance_active_count=active_count,
                maintenance_max_active=manager.max_active,
                maintenance_remaining_overflow=max(
                    0,
                    active_count + required_slots - manager.max_active,
                ),
            )
            return False

        for _ in range(maintainer.config.max_actions):
            active_count = await manager.active_count()
            remaining = active_count + required_slots - manager.max_active
            if remaining <= 0:
                return True
            records = await manager.retention_candidates(
                limit=maintainer.config.candidate_limit
            )
            record_snapshots = {record.id: record for record in records}
            candidates = tuple(
                MemoryMaintenanceCandidate(
                    id=record.id,
                    title=record.title,
                    summary=record.summary,
                    content=record.content,
                    created_at=record.created_at,
                    updated_at=record.updated_at,
                    last_accessed_at=record.last_accessed_at,
                    access_count=record.access_count,
                )
                for record in records
            )
            candidate_ids = tuple(candidate.id for candidate in candidates)
            if not candidates:
                await emitter.emit(
                    AgentEventType.MEMORY_MAINTENANCE_FAILED,
                    maintenance_triggered=True,
                    maintenance_error="no active maintenance candidates",
                    maintenance_active_count=active_count,
                    maintenance_max_active=manager.max_active,
                    maintenance_remaining_overflow=max(0, remaining),
                )
                return False
            await emitter.emit(
                AgentEventType.MEMORY_MAINTENANCE_STARTED,
                maintenance_triggered=True,
                maintenance_active_count=active_count,
                maintenance_max_active=manager.max_active,
                maintenance_candidate_ids=candidate_ids,
                maintenance_remaining_overflow=max(0, remaining),
                provider=maintainer.provider_hint,
                model=maintainer.model_hint,
            )
            proposal = await maintainer.decide(
                MemoryMaintenanceInput(
                    active_count=active_count,
                    max_active=manager.max_active,
                    required_slots=required_slots,
                    candidates=candidates,
                )
            )
            if proposal.error is not None or proposal.decision is None:
                await emitter.emit(
                    AgentEventType.MEMORY_MAINTENANCE_FAILED,
                    maintenance_triggered=True,
                    maintenance_duration_ms=proposal.duration_ms,
                    maintenance_error=(
                        proposal.error or "maintenance model returned no decision"
                    ),
                    maintenance_active_count=active_count,
                    maintenance_max_active=manager.max_active,
                    maintenance_candidate_ids=candidate_ids,
                    maintenance_remaining_overflow=max(0, remaining),
                    provider=proposal.provider,
                    model=proposal.model,
                    usage=proposal.usage,
                )
                return False

            decision = proposal.decision
            if decision.action is MaintenanceAction.DEFER:
                await emitter.emit(
                    AgentEventType.MEMORY_MAINTENANCE_COMPLETED,
                    maintenance_triggered=True,
                    maintenance_action=decision.action.value,
                    maintenance_duration_ms=proposal.duration_ms,
                    maintenance_reason=decision.reason,
                    maintenance_active_count=active_count,
                    maintenance_max_active=manager.max_active,
                    maintenance_candidate_ids=candidate_ids,
                    maintenance_remaining_overflow=max(0, remaining),
                    provider=proposal.provider,
                    model=proposal.model,
                    usage=proposal.usage,
                )
                return False

            memory_id = decision.memory_id or ""
            snapshots = {candidate.id: candidate for candidate in candidates}
            snapshot = snapshots.get(memory_id)
            if snapshot is None:
                await emitter.emit(
                    AgentEventType.MEMORY_MAINTENANCE_FAILED,
                    maintenance_triggered=True,
                    maintenance_action=decision.action.value,
                    maintenance_duration_ms=proposal.duration_ms,
                    maintenance_error=(
                        "maintenance selected an ID outside the candidate set"
                    ),
                    maintenance_memory_id=memory_id,
                    maintenance_active_count=active_count,
                    maintenance_max_active=manager.max_active,
                    maintenance_candidate_ids=candidate_ids,
                    maintenance_remaining_overflow=max(0, remaining),
                    provider=proposal.provider,
                    model=proposal.model,
                    usage=proposal.usage,
                )
                return False
            try:
                archived = await manager.archive_if_unchanged(
                    memory_id,
                    expected_record=record_snapshots[memory_id],
                    reason=decision.reason,
                )
            except Exception as exc:
                await emitter.emit(
                    AgentEventType.MEMORY_MAINTENANCE_FAILED,
                    maintenance_triggered=True,
                    maintenance_action=decision.action.value,
                    maintenance_duration_ms=proposal.duration_ms,
                    maintenance_error=f"{type(exc).__name__}: {exc}",
                    maintenance_memory_id=memory_id,
                    maintenance_reason=decision.reason,
                    maintenance_active_count=active_count,
                    maintenance_max_active=manager.max_active,
                    maintenance_candidate_ids=candidate_ids,
                    maintenance_remaining_overflow=max(0, remaining),
                    provider=proposal.provider,
                    model=proposal.model,
                    usage=proposal.usage,
                )
                return False
            after_count = await manager.active_count()
            after_remaining = max(
                0,
                after_count + required_slots - manager.max_active,
            )
            await emitter.emit(
                AgentEventType.MEMORY_MAINTENANCE_COMPLETED,
                maintenance_triggered=True,
                maintenance_action=decision.action.value,
                maintenance_duration_ms=proposal.duration_ms,
                maintenance_memory_id=archived.id,
                maintenance_reason=decision.reason,
                maintenance_active_count=after_count,
                maintenance_max_active=manager.max_active,
                maintenance_candidate_ids=candidate_ids,
                maintenance_remaining_overflow=after_remaining,
                provider=proposal.provider,
                model=proposal.model,
                usage=proposal.usage,
            )

        active_count = await manager.active_count()
        remaining = max(
            0,
            active_count + required_slots - manager.max_active,
        )
        if remaining == 0:
            return True
        await emitter.emit(
            AgentEventType.MEMORY_MAINTENANCE_SKIPPED,
            maintenance_triggered=True,
            maintenance_skip_reason="max_actions_reached",
            maintenance_active_count=active_count,
            maintenance_max_active=manager.max_active,
            maintenance_remaining_overflow=remaining,
        )
        return False

    async def run_stream(
        self,
        user_input: str,
        *,
        history: Sequence[Message] = (),
        conversation_id: str | None = None,
        event_handler: AgentEventHandler | None = None,
        summary_state: ConversationSummaryState | None = None,
        run_id: str | None = None,
        recovery_run_id: str | None = None,
        mode: AgentMode = AgentMode.NORMAL,
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
                    run_id=run_id,
                    recovery_run_id=recovery_run_id,
                    mode=mode,
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
        plan_task_id: str | None = None,
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
            plan_task_id=plan_task_id,
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


def _computer_verification_status(output: object) -> str | None:
    """从统一工具输出中读取电脑输入的效果验证状态。"""

    if not isinstance(output, str):
        return None
    try:
        payload = json.loads(output)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    status = payload.get("verification_status")
    return status if isinstance(status, str) else None


def _looks_like_textual_tool_call(content: str | None) -> bool:
    """识别被模型错误输出为普通文本的常见工具协议标记。"""

    if not content:
        return False
    lowered = content.lower()
    return any(
        marker in lowered
        for marker in (
            "<tool_calls",
            "<｜｜dsml｜｜tool_calls",
            "<｜｜dsml｜｜invoke",
        )
    )


def _skill_read_activated_name(output: object) -> str | None:
    """从 skill_read 的工具结果中提取被命中激活的 skill 名。"""
    if isinstance(output, str):
        try:
            payload = json.loads(output)
        except (ValueError, TypeError):
            return None
    elif isinstance(output, dict):
        payload = output
    else:
        return None
    if not payload.get("found"):
        return None
    name = payload.get("name")
    return name if isinstance(name, str) and name else None


def _plan_failure_message(message: Message, prefix: str) -> Message:
    """Plan Mode 未形成有效计划时，在最终回复前附加明确提示。"""

    content = message.content or ""
    return message.model_copy(update={"content": f"{prefix}\n\n{content}"})


def _plan_task_id_from_output(output: object) -> str | None:
    """从 task_create / task_update 的工具输出 JSON 中提取任务 ID。"""

    if isinstance(output, str):
        try:
            payload = json.loads(output)
        except (ValueError, TypeError):
            return None
    elif isinstance(output, dict):
        payload = output
    else:
        return None
    if not isinstance(payload, dict):
        return None
    task_id = payload.get("id")
    return task_id if isinstance(task_id, str) and task_id else None


def _add_usage(total: ModelUsage, current: ModelUsage) -> ModelUsage:
    """累加多轮模型调用的 token 用量。"""

    return add_model_usage(total, current)


def _usage_call_count(usage: ModelUsage) -> int:
    """从附属模型 Usage 中取得调用数；旧实现仅有 Token 时推断为一次。"""

    if usage.model_calls > 0:
        return usage.model_calls
    if usage.input_tokens or usage.output_tokens or usage.total_tokens:
        return 1
    return 0


def _run_budget_detail(decision: RunBudgetDecision) -> str:
    """生成稳定、可诊断的预算停止原因。"""

    reason = decision.reason.value if decision.reason is not None else "unknown"
    return (
        f"reason={reason}, chargeable_tokens={decision.chargeable_tokens}, "
        f"model_calls={decision.model_calls}"
    )


def _run_budget_event_fields(
    decision: RunBudgetDecision,
    config: RunBudgetConfig,
    *,
    status: RunBudgetStatus | None = None,
) -> dict[str, object]:
    """把预算快照转换为 AgentEvent 的统一字段。"""

    return {
        "run_budget_status": (status or decision.status).value,
        "run_budget_reason": (
            decision.reason.value if decision.reason is not None else None
        ),
        "run_budget_chargeable_tokens": decision.chargeable_tokens,
        "run_budget_model_calls": decision.model_calls,
        "run_budget_warning_tokens": config.warning_tokens,
        "run_budget_finalization_tokens": config.finalization_tokens,
        "run_budget_hard_tokens": config.hard_tokens,
        "run_budget_warning_model_calls": config.warning_model_calls,
        "run_budget_finalization_model_calls": config.finalization_model_calls,
        "run_budget_hard_model_calls": config.hard_model_calls,
    }


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


def _recalled_memory_revisions(
    records: Sequence[ToolCallRecord],
) -> dict[str, int]:
    """返回本轮确实读到的 Memory ID 与当时的语义 revision。"""

    recalled: dict[str, int] = {}
    for record in records:
        if record.tool_call.name != "memory_read" or not record.result.success:
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
        revision = output.get("revision") if isinstance(output, dict) else None
        if (
            isinstance(output, dict)
            and output.get("found") is True
            and output.get("id") == normalized
            and isinstance(revision, int)
            and revision > 0
        ):
            recalled[normalized] = revision
    return recalled


def _without_legacy_fixed_date(message: Message) -> Message:
    """仅清理模型请求副本中的旧固定日期，保留数据库原始历史。"""

    if message.role is not MessageRole.SYSTEM or not message.content:
        return message
    cleaned = _LEGACY_DATE_PATTERN.sub("", message.content)
    if cleaned == message.content:
        return message
    return message.model_copy(update={"content": cleaned})


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
