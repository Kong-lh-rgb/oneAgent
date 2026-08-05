"""上下文管理器：决定每次模型调用实际发送的上下文。

当前阶段只做 token 估算、查询模型能力、计算输入预算并判断是否达到
压缩触发线；**不真正压缩**——messages 与 tools 原样返回。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.models.types import Message, ToolDefinition

from .budget import ContextBudgetPolicy, build_budget_policy
from .capabilities import (
    ModelCapabilityRegistry,
    build_model_capability_registry,
)
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
    capability_source: str | None = None
    trimmed: bool = False
    reason: str | None = None


class ContextManager:
    """准备模型请求上下文；当前不压缩，原样返回。"""

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
    ) -> ContextDecision:
        """返回要发送的上下文、估算与预算状态（不裁剪消息）。"""

        capabilities = self._registry.lookup(provider, model)
        budget = self._budget_policy.compute(
            capabilities,
            max_output_tokens=max_output_tokens,
        )
        estimated = self._estimator.estimate_request(
            messages,
            tools=tools,
            model=model,
            provider=provider,
        )
        usage_ratio = (
            estimated / budget.input_budget if budget.input_budget > 0 else None
        )
        requires_compaction = estimated >= budget.trigger_tokens
        reason = (
            f"estimated={estimated};input_budget={budget.input_budget};"
            f"trigger={budget.trigger_tokens};target={budget.target_tokens};"
            f"requires_compaction={requires_compaction}"
        )
        return ContextDecision(
            messages=tuple(messages),
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
            capability_source=capabilities.source.value,
            trimmed=False,
            reason=reason,
        )


__all__ = ["ContextDecision", "ContextManager"]
