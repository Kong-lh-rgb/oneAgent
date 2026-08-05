"""上下文管理器：从完整历史构造每次模型调用实际发送的上下文。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.models.types import Message, ToolDefinition

from .budget import ContextBudgetPolicy, build_budget_policy
from .capabilities import (
    ModelCapabilityRegistry,
    build_model_capability_registry,
)
from .history import compact_model_history
from .tokens import TokenEstimator, default_token_estimator


@dataclass(frozen=True)
class ContextDecision:
    """一次模型调用最终发送的上下文与预算状态。"""

    messages: tuple[Message, ...]
    tools: tuple[ToolDefinition, ...]
    provider: str | None = None
    model: str | None = None
    estimated_input_tokens: int | None = None
    context_window: int | None = None
    reserved_output_tokens: int | None = None
    safety_margin_tokens: int | None = None
    input_budget: int | None = None
    trigger_tokens: int | None = None
    target_tokens: int | None = None
    usage_ratio: float | None = None
    requires_compaction: bool = False
    exceeds_input_budget: bool = False
    capability_source: str | None = None
    trimmed: bool = False
    reason: str | None = None


class ContextManager:
    """准备模型请求上下文，不修改调用方持有的原始历史。"""

    def __init__(
        self,
        estimator: TokenEstimator | None = None,
        *,
        registry: ModelCapabilityRegistry | None = None,
        budget_policy: ContextBudgetPolicy | None = None,
    ) -> None:
        self._estimator = estimator or default_token_estimator()
        self._registry = registry or build_model_capability_registry()
        self._budget_policy = budget_policy or build_budget_policy()

    @property
    def estimator(self) -> TokenEstimator:
        return self._estimator

    @property
    def registry(self) -> ModelCapabilityRegistry:
        return self._registry

    async def prepare(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolDefinition] = (),
        model: str | None = None,
        provider: str | None = None,
        max_output_tokens: int | None = None,
        history_count: int | None = None,
        keep_recent_tool_rounds: int = 0,
    ) -> ContextDecision:
        """返回模型请求上下文、估算与预算状态。

        ``history_count`` 标记消息序列中已经持久化的历史前缀。只有这个前缀
        会移除旧工具协议；当前 Run 新增的消息保持完整，确保工具调用与工具
        结果仍能按 Provider 协议继续发送。

        ``keep_recent_tool_rounds`` 控制历史前缀中保留最近多少轮完整工具协议；
        更旧的工具轮会被降级移除。默认 0 表示全部移除（旧行为）。
        """

        if history_count is None:
            history_count = 0
        if history_count < 0 or history_count > len(messages):
            raise ValueError("history_count must be within the messages range")

        request_messages = (
            *compact_model_history(
                messages[:history_count],
                keep_recent_tool_rounds=keep_recent_tool_rounds,
            ),
            *messages[history_count:],
        )
        trimmed = request_messages != tuple(messages)

        capabilities = self._registry.lookup(provider, model)
        budget = self._budget_policy.compute(
            capabilities,
            max_output_tokens=max_output_tokens,
        )
        estimated = self._estimator.estimate_request(
            request_messages,
            tools=tools,
            model=model,
            provider=provider,
        )
        usage_ratio = (
            estimated / budget.input_budget if budget.input_budget > 0 else None
        )
        requires_compaction = estimated >= budget.trigger_tokens
        exceeds_input_budget = estimated > budget.input_budget
        reason = (
            f"estimated={estimated};input_budget={budget.input_budget};"
            f"trigger={budget.trigger_tokens};target={budget.target_tokens};"
            f"requires_compaction={requires_compaction};"
            f"exceeds_input_budget={exceeds_input_budget};trimmed={trimmed};"
            f"keep_recent_tool_rounds={keep_recent_tool_rounds}"
        )
        return ContextDecision(
            messages=request_messages,
            tools=tuple(tools),
            provider=capabilities.provider,
            model=capabilities.model,
            estimated_input_tokens=estimated,
            context_window=budget.context_window,
            reserved_output_tokens=budget.reserved_output_tokens,
            safety_margin_tokens=budget.safety_margin_tokens,
            input_budget=budget.input_budget,
            trigger_tokens=budget.trigger_tokens,
            target_tokens=budget.target_tokens,
            usage_ratio=usage_ratio,
            requires_compaction=requires_compaction,
            exceeds_input_budget=exceeds_input_budget,
            capability_source=capabilities.source.value,
            trimmed=trimmed,
            reason=reason,
        )


__all__ = ["ContextDecision", "ContextManager"]
