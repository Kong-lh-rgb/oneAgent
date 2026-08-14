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
    compact_model_history,
)
from app.models.types import Message, MessageRole, ToolCall


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
    assert budget.working_input_budget == 32_768
    assert budget.hard_trigger_tokens == int(budget.input_budget * 0.8)
    assert budget.hard_target_tokens == int(budget.input_budget * 0.6)
    assert budget.trigger_tokens == int(32_768 * 0.7)
    assert budget.target_tokens == int(32_768 * 0.45)
    assert budget.tool_result_budget_tokens == int(budget.target_tokens * 0.35)


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


def test_requested_output_cannot_exceed_model_maximum() -> None:
    policy = ContextBudgetPolicy(safety_margin_tokens=0)

    with pytest.raises(ValueError, match="model maximum"):
        policy.compute(_caps(max_output=1_000), max_output_tokens=1_001)


def test_invalid_ratio_config_raises() -> None:
    with pytest.raises(ValueError, match="trigger_ratio"):
        ContextBudgetPolicy(trigger_ratio=1.5)
    with pytest.raises(ValueError, match="target_ratio"):
        ContextBudgetPolicy(target_ratio=0.9)
    with pytest.raises(ValueError, match="target_ratio"):
        ContextBudgetPolicy(trigger_ratio=0.6, target_ratio=0.7)


def test_compact_model_history_only_removes_tool_protocol() -> None:
    call = ToolCall(id="search-1", name="web_search", arguments={"query": "AI"})
    messages = (
        Message(role=MessageRole.SYSTEM, content="系统提示"),
        Message(role=MessageRole.USER, content="搜索新闻"),
        Message(role=MessageRole.ASSISTANT, tool_calls=(call,)),
        Message(
            role=MessageRole.TOOL,
            tool_call_id=call.id,
            name=call.name,
            content="很长的搜索结果",
        ),
        Message(role=MessageRole.ASSISTANT, content="新闻摘要"),
    )

    compacted = compact_model_history(messages)

    assert [message.role for message in compacted] == [
        MessageRole.SYSTEM,
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]
    assert compacted[-1].content == "新闻摘要"


@pytest.mark.asyncio
async def test_context_manager_below_trigger_preserves_complete_protocol() -> None:
    manager = ContextManager(keep_recent_tool_rounds=0)
    old_call = ToolCall(
        id="old-search",
        name="web_search",
        arguments={"query": "旧查询"},
    )
    current_call = ToolCall(
        id="current-search",
        name="web_search",
        arguments={"query": "新查询"},
    )
    history = (
        Message(role=MessageRole.USER, content="上一轮"),
        Message(role=MessageRole.ASSISTANT, tool_calls=(old_call,)),
        Message(
            role=MessageRole.TOOL,
            tool_call_id=old_call.id,
            name=old_call.name,
            content="旧工具输出",
        ),
        Message(role=MessageRole.ASSISTANT, content="上一轮答案"),
    )
    current_run = (
        Message(role=MessageRole.USER, content="这一轮"),
        Message(role=MessageRole.ASSISTANT, tool_calls=(current_call,)),
        Message(
            role=MessageRole.TOOL,
            tool_call_id=current_call.id,
            name=current_call.name,
            content="当前工具输出",
        ),
    )

    decision = await manager.prepare(
        (*history, *current_run),
        history_count=len(history),
        model="qwen3.7-plus",
        provider="qwen",
    )

    assert decision.messages == (*history, *current_run)
    assert decision.trimmed is False
    assert decision.requires_compaction is False
    assert decision.compacted_tool_results == 0
    assert decision.removed_tool_rounds == 0
    assert history[1].tool_calls == (old_call,)
    assert history[2].content == "旧工具输出"


@pytest.mark.asyncio
async def test_short_request_stays_below_working_trigger() -> None:
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
async def test_small_window_uses_stricter_working_ratio_as_effective_budget() -> None:
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
    assert decision.working_input_budget == 150
    assert decision.hard_trigger_tokens == 120
    assert decision.trigger_tokens == int(150 * 0.7)
    assert decision.target_tokens == int(150 * 0.45)
    assert decision.capability_source == CapabilitySource.OVERRIDE.value
