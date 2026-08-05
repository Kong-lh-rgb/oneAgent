"""上下文管理器：从完整历史构造每次模型调用实际发送的上下文。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.models.types import Message, ToolDefinition

from .blocks import partition_messages
from .budget import ContextBudgetPolicy, build_budget_policy
from .capabilities import (
    ModelCapabilityRegistry,
    build_model_capability_registry,
)
from .config import ContextSettings
from .reducers import ToolReducer
from .tokens import TokenEstimator, default_token_estimator


class ContextCompactionStage(StrEnum):
    """本次模型请求实际执行到的压缩阶段。"""

    NONE = "none"
    TOOL_RESULTS = "tool_results"
    TOOL_ROUNDS = "tool_rounds"
    TOOL_RESULTS_AND_ROUNDS = "tool_results_and_rounds"


@dataclass(frozen=True)
class ContextDecision:
    """一次模型调用最终发送的上下文与预算状态。"""

    messages: tuple[Message, ...]
    tools: tuple[ToolDefinition, ...]
    provider: str | None = None
    model: str | None = None
    original_estimated_input_tokens: int | None = None
    prepared_input_tokens: int | None = None
    estimated_input_tokens: int | None = None
    context_window: int | None = None
    reserved_output_tokens: int | None = None
    safety_margin_tokens: int | None = None
    input_budget: int | None = None
    trigger_tokens: int | None = None
    target_tokens: int | None = None
    original_usage_ratio: float | None = None
    prepared_usage_ratio: float | None = None
    usage_ratio: float | None = None
    requires_compaction: bool = False
    exceeds_input_budget: bool = False
    capability_source: str | None = None
    trimmed: bool = False
    compaction_stage: ContextCompactionStage = ContextCompactionStage.NONE
    reached_target: bool = True
    needs_next_compaction_stage: bool = False
    compacted_tool_results: int = 0
    removed_tool_rounds: int = 0
    reason: str | None = None


class ContextManager:
    """准备模型请求上下文，不修改调用方持有的原始历史。"""

    def __init__(
        self,
        estimator: TokenEstimator | None = None,
        *,
        registry: ModelCapabilityRegistry | None = None,
        budget_policy: ContextBudgetPolicy | None = None,
        context_settings: ContextSettings | None = None,
        keep_recent_tool_rounds: int | None = None,
        tool_reducer: ToolReducer | None = None,
    ) -> None:
        settings = context_settings or ContextSettings()
        resolved_keep_recent_tool_rounds = (
            settings.context_keep_recent_tool_rounds
            if keep_recent_tool_rounds is None
            else keep_recent_tool_rounds
        )
        if resolved_keep_recent_tool_rounds < 0:
            raise ValueError("keep_recent_tool_rounds cannot be negative")
        self._estimator = estimator or default_token_estimator()
        self._registry = registry or build_model_capability_registry(
            context_settings=settings
        )
        self._budget_policy = budget_policy or build_budget_policy(settings)
        self._tool_reducer = tool_reducer or ToolReducer(
            keep_recent_tool_rounds=resolved_keep_recent_tool_rounds,
            max_tool_result_chars=settings.context_max_tool_result_chars,
            tool_result_head_chars=settings.context_tool_result_head_chars,
            tool_result_tail_chars=settings.context_tool_result_tail_chars,
        )

    @property
    def estimator(self) -> TokenEstimator:
        return self._estimator

    @property
    def registry(self) -> ModelCapabilityRegistry:
        return self._registry

    @property
    def keep_recent_tool_rounds(self) -> int:
        """模型请求默认保留的最近历史工具轮数。"""

        return self._tool_reducer.keep_recent_tool_rounds

    async def prepare(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolDefinition] = (),
        model: str | None = None,
        provider: str | None = None,
        max_output_tokens: int | None = None,
        history_count: int | None = None,
        keep_recent_tool_rounds: int | None = None,
    ) -> ContextDecision:
        """返回模型请求上下文、估算与预算状态。

        ``history_count`` 标记消息序列中已经持久化的历史前缀。只有这个前缀
        中符合条件的旧工具协议允许被压缩；当前 Run 新增的消息保持完整，
        确保工具调用与工具结果仍能按 Provider 协议继续发送。

        仅当完整候选上下文达到压缩触发线时才执行 ToolReducer。普通情况下
        原样返回全部历史消息；当前 Run 消息永远不参与工具层压缩。
        """

        if history_count is None:
            history_count = 0
        if history_count < 0 or history_count > len(messages):
            raise ValueError("history_count must be within the messages range")
        resolved_keep_recent_tool_rounds = (
            self._tool_reducer.keep_recent_tool_rounds
            if keep_recent_tool_rounds is None
            else keep_recent_tool_rounds
        )
        if resolved_keep_recent_tool_rounds < 0:
            raise ValueError("keep_recent_tool_rounds cannot be negative")

        original_messages = tuple(messages)

        capabilities = self._registry.lookup(provider, model)
        budget = self._budget_policy.compute(
            capabilities,
            max_output_tokens=max_output_tokens,
        )
        original_estimated = self._estimator.estimate_request(
            original_messages,
            tools=tools,
            model=model,
            provider=provider,
        )
        original_usage_ratio = (
            original_estimated / budget.input_budget
            if budget.input_budget > 0
            else None
        )
        requires_compaction = original_estimated >= budget.trigger_tokens

        request_messages = original_messages
        prepared_input_tokens = original_estimated
        compacted_tool_results = 0
        removed_tool_rounds = 0
        compaction_stage = ContextCompactionStage.NONE
        reached_target = original_estimated <= budget.target_tokens

        if requires_compaction:
            history_blocks = partition_messages(
                original_messages[:history_count]
            )
            current_messages = original_messages[history_count:]

            def estimate(candidate: tuple[Message, ...]) -> int:
                return self._estimator.estimate_request(
                    candidate,
                    tools=tools,
                    model=model,
                    provider=provider,
                )

            reduction = self._tool_reducer.reduce(
                history_blocks,
                current_messages=current_messages,
                initial_estimated_input_tokens=original_estimated,
                target_tokens=budget.target_tokens,
                estimate=estimate,
                keep_recent_tool_rounds=resolved_keep_recent_tool_rounds,
            )
            request_messages = reduction.messages
            prepared_input_tokens = reduction.estimated_input_tokens
            compacted_tool_results = reduction.compacted_tool_results
            removed_tool_rounds = reduction.removed_tool_rounds
            reached_target = reduction.reached_target
            if compacted_tool_results and removed_tool_rounds:
                compaction_stage = ContextCompactionStage.TOOL_RESULTS_AND_ROUNDS
            elif removed_tool_rounds:
                compaction_stage = ContextCompactionStage.TOOL_ROUNDS
            elif compacted_tool_results:
                compaction_stage = ContextCompactionStage.TOOL_RESULTS

        prepared_usage_ratio = (
            prepared_input_tokens / budget.input_budget
            if budget.input_budget > 0
            else None
        )
        trimmed = request_messages != original_messages
        needs_next_compaction_stage = requires_compaction and not reached_target
        exceeds_input_budget = prepared_input_tokens > budget.input_budget
        reason = (
            f"original_estimated={original_estimated};"
            f"prepared_input_tokens={prepared_input_tokens};"
            f"input_budget={budget.input_budget};"
            f"trigger={budget.trigger_tokens};target={budget.target_tokens};"
            f"requires_compaction={requires_compaction};"
            f"exceeds_input_budget={exceeds_input_budget};trimmed={trimmed};"
            f"compaction_stage={compaction_stage.value};"
            f"compacted_tool_results={compacted_tool_results};"
            f"removed_tool_rounds={removed_tool_rounds};"
            f"reached_target={reached_target};"
            f"needs_next_compaction_stage={needs_next_compaction_stage}"
        )
        return ContextDecision(
            messages=request_messages,
            tools=tuple(tools),
            provider=capabilities.provider,
            model=capabilities.model,
            original_estimated_input_tokens=original_estimated,
            prepared_input_tokens=prepared_input_tokens,
            estimated_input_tokens=prepared_input_tokens,
            context_window=budget.context_window,
            reserved_output_tokens=budget.reserved_output_tokens,
            safety_margin_tokens=budget.safety_margin_tokens,
            input_budget=budget.input_budget,
            trigger_tokens=budget.trigger_tokens,
            target_tokens=budget.target_tokens,
            original_usage_ratio=original_usage_ratio,
            prepared_usage_ratio=prepared_usage_ratio,
            usage_ratio=prepared_usage_ratio,
            requires_compaction=requires_compaction,
            exceeds_input_budget=exceeds_input_budget,
            capability_source=capabilities.source.value,
            trimmed=trimmed,
            compaction_stage=compaction_stage,
            reached_target=reached_target,
            needs_next_compaction_stage=needs_next_compaction_stage,
            compacted_tool_results=compacted_tool_results,
            removed_tool_rounds=removed_tool_rounds,
            reason=reason,
        )


__all__ = ["ContextCompactionStage", "ContextDecision", "ContextManager"]
