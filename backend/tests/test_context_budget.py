"""上下文预算策略与压缩触发线测试。"""

from __future__ import annotations

import pytest

from app.context import (
    CapabilitySource,
    ContextBudgetPolicy,
    ContextManager,
    ContextSettings,
    ModelCapabilities,
    build_budget_policy,
    build_model_capability_registry,
)
from app.models.types import Message, MessageRole


def _caps(
    context_window: int = 131_072,
    max_output: int = 8_192,
) -> ModelCapabilities:
    return ModelCapabilities(
        provider="qwen",
        model="qwen3.7-plus",
        context_window=context_window,
        max_output_tokens=max_output,
        source=CapabilitySource.BUILTIN,
    )


def test_budget_formula() -> None:
    policy = ContextBudgetPolicy(safety_margin_tokens=4_096)

    budget = policy.compute(_caps(), max_output_tokens=4_000)

    assert budget.context_window == 131_072
    assert budget.reserved_output_tokens == 4_000
    assert budget.safety_margin_tokens == 4_096
    assert budget.input_budget == 131_072 - 4_000 - 4_096
    assert budget.trigger_tokens == int(budget.input_budget * 0.8)
    assert budget.target_tokens == int(budget.input_budget * 0.6)


def test_model_default_max_output_used_when_no_override() -> None:
    policy = ContextBudgetPolicy(safety_margin_tokens=0)

    budget = policy.compute(_caps(max_output=8_192))

    assert budget.reserved_output_tokens == 8_192
    assert budget.input_budget == 131_072 - 8_192


def test_explicit_max_output_preferred() -> None:
    policy = ContextBudgetPolicy(safety_margin_tokens=0)

    budget = policy.compute(_caps(max_output=8_192), max_output_tokens=2_000)

    assert budget.reserved_output_tokens == 2_000
    assert budget.input_budget == 131_072 - 2_000


def test_negative_budget_raises_clear_error() -> None:
    policy = ContextBudgetPolicy(safety_margin_tokens=0)

    with pytest.raises(ValueError, match="input_budget"):
        policy.compute(_caps(context_window=10, max_output=50))


def test_invalid_ratio_config_raises() -> None:
    with pytest.raises(ValueError, match="trigger_ratio"):
        ContextBudgetPolicy(trigger_ratio=1.5)
    with pytest.raises(ValueError, match="target_ratio"):
        ContextBudgetPolicy(target_ratio=0.9)
    with pytest.raises(ValueError, match="target_ratio"):
        ContextBudgetPolicy(trigger_ratio=0.6, target_ratio=0.7)


@pytest.mark.asyncio
async def test_requires_compaction_below_80_is_false() -> None:
    manager = ContextManager(
        registry=build_model_capability_registry(
            context_settings=ContextSettings(_env_file=None),
        ),
        budget_policy=build_budget_policy(ContextSettings(_env_file=None)),
    )

    decision = await manager.prepare(
        (Message(role=MessageRole.USER, content="hi"),),
        model="qwen3.7-plus",
        provider="qwen",
    )

    assert decision.requires_compaction is False
    assert decision.usage_ratio is not None
    assert decision.usage_ratio < 0.8


@pytest.mark.asyncio
async def test_requires_compaction_at_or_above_80_is_true() -> None:
    registry = build_model_capability_registry(
        context_settings=ContextSettings(_env_file=None),
    )
    # 用极小窗口 + 长消息触发压缩线：window=200, reserved=50, margin=0 → trigger=120
    registry.register_override(
        "qwen",
        "qwen3.7-plus",
        context_window=200,
        max_output_tokens=50,
    )
    manager = ContextManager(
        registry=registry,
        budget_policy=ContextBudgetPolicy(safety_margin_tokens=0),
    )

    decision = await manager.prepare(
        (Message(role=MessageRole.USER, content="x" * 2000),),
        model="qwen3.7-plus",
        provider="qwen",
    )

    assert decision.requires_compaction is True
    assert decision.context_window == 200
    assert decision.input_budget == 150
    assert decision.trigger_tokens == 120
    assert decision.target_tokens == 90
    assert decision.capability_source == CapabilitySource.OVERRIDE.value
