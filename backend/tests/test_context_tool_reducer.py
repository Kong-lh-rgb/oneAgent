"""工具消息第一层压缩测试。"""

from __future__ import annotations

import json
from collections.abc import Sequence

import pytest

from app.context import (
    ContextBudgetPolicy,
    ContextCompactionStage,
    ContextManager,
    ContextSettings,
    MalformedToolBlock,
    ModelCapabilityRegistry,
    ToolReducer,
    partition_messages,
)
from app.models.types import Message, MessageRole, ToolCall, ToolDefinition


class CharacterEstimator:
    """用字符数提供可预测的离线估算，并记录每次重估。"""

    def __init__(self) -> None:
        self.requests: list[tuple[Message, ...]] = []

    def estimate_request(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolDefinition] = (),
        model: str | None = None,
        provider: str | None = None,
    ) -> int:
        snapshot = tuple(messages)
        self.requests.append(snapshot)
        return sum(20 + len(message.content or "") for message in snapshot) + sum(
            len(tool.name) for tool in tools
        )

    def estimate_messages(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        provider: str | None = None,
    ) -> int:
        return sum(20 + len(message.content or "") for message in messages)

    def estimate_tools(
        self,
        tools: Sequence[ToolDefinition],
        *,
        model: str | None = None,
        provider: str | None = None,
    ) -> int:
        return sum(len(tool.name) for tool in tools)


class NeverCalledToolReducer(ToolReducer):
    """低于触发线时调用即使测试失败。"""

    def reduce(self, *args, **kwargs):
        raise AssertionError("ToolReducer must not run below trigger")


def _tool_round(
    call_id: str,
    content: str,
    *,
    second_result: str | None = None,
) -> tuple[Message, ...]:
    calls = [ToolCall(id=call_id, name="search", arguments={})]
    results = [
        Message(
            role=MessageRole.TOOL,
            name="search",
            tool_call_id=call_id,
            content=content,
        )
    ]
    if second_result is not None:
        second_id = f"{call_id}-second"
        calls.append(ToolCall(id=second_id, name="read", arguments={}))
        results.append(
            Message(
                role=MessageRole.TOOL,
                name="read",
                tool_call_id=second_id,
                content=second_result,
            )
        )
    return (
        Message(role=MessageRole.ASSISTANT, tool_calls=tuple(calls)),
        *results,
    )


def _estimate(messages: Sequence[Message]) -> int:
    return sum(20 + len(message.content or "") for message in messages)


def test_tool_reducer_shortens_only_unprotected_old_results() -> None:
    older = _tool_round("old", "H" * 500 + "T" * 500)
    recent_one = _tool_round("recent-1", "R" * 300)
    recent_two = _tool_round("recent-2", "S" * 300)
    original = (*older, *recent_one, *recent_two)
    blocks = partition_messages(original)
    estimator_calls: list[tuple[Message, ...]] = []

    def estimate(messages: tuple[Message, ...]) -> int:
        estimator_calls.append(messages)
        return _estimate(messages)

    initial = _estimate(original)
    result = ToolReducer(
        keep_recent_tool_rounds=2,
        max_tool_result_chars=100,
        tool_result_head_chars=20,
        tool_result_tail_chars=20,
    ).reduce(
        blocks,
        current_messages=(),
        initial_estimated_input_tokens=initial,
        target_tokens=initial - 1,
        estimate=estimate,
    )

    assert result.reached_target is True
    assert result.compacted_tool_results == 1
    assert result.removed_tool_rounds == 0
    assert len(estimator_calls) == 1
    compacted = result.messages[1]
    assert compacted.name == "search"
    assert compacted.tool_call_id == "old"
    assert (compacted.content or "").startswith("H" * 20)
    assert (compacted.content or "").endswith("T" * 20)
    assert "original_chars=1000" in (compacted.content or "")
    assert "omitted 960 characters" in (compacted.content or "")
    assert result.messages[-1] == recent_two[-1]
    assert original[1].content == "H" * 500 + "T" * 500


def test_computer_observation_compaction_keeps_valid_semantic_json() -> None:
    payload = {
        "id": "obs-1",
        "active_app": {"name": "Notes"},
        "target": {"name": "Notes", "pid": 42},
        "target_is_frontmost": False,
        "active_window": {"ref": "w1", "title": "Notes"},
        "focused_element_ref": "editor",
        "truncated": True,
        "element_stats": {
            "observed": 1800,
            "returned": 101,
            "editable_count": 1,
            "actionable_count": 2,
            "repetitive_elements_dropped": 1200,
        },
        "elements": [
            {
                "ref": "editor",
                "role": "text_area",
                "focused": True,
                "editable": True,
                "value": "重要编辑内容",
            },
            *(
                {
                    "ref": f"sidebar-{index}",
                    "role": "cell",
                    "title": "侧边栏" + "x" * 80,
                }
                for index in range(100)
            ),
        ],
    }
    round_messages = (
        Message(
            role=MessageRole.ASSISTANT,
            tool_calls=(ToolCall(id="observe-1", name="computer_observe"),),
        ),
        Message(
            role=MessageRole.TOOL,
            name="computer_observe",
            tool_call_id="observe-1",
            content=json.dumps(payload, ensure_ascii=False),
        ),
    )
    reducer = ToolReducer(
        keep_recent_tool_rounds=0,
        max_tool_result_chars=700,
        tool_result_head_chars=100,
        tool_result_tail_chars=100,
    )
    result = reducer.reduce(
        partition_messages(round_messages),
        current_messages=(),
        initial_estimated_input_tokens=10_000,
        target_tokens=2_000,
        estimate=_estimate,
    )

    compacted = json.loads(result.messages[1].content or "{}")
    assert compacted["target"] == {"name": "Notes", "pid": 42}
    assert compacted["target_is_frontmost"] is False
    assert compacted["element_stats"]["editable_count"] == 1
    assert compacted["focused_element_ref"] == "editor"
    assert compacted["elements"][0]["ref"] == "editor"
    assert compacted["compaction"]["kind"] == "semantic_observation"
    assert len(result.messages[1].content or "") <= 700


def test_tool_reducer_removes_oldest_round_and_stops_at_target() -> None:
    first = _tool_round("first", "a" * 10)
    second = _tool_round("second", "b" * 10)
    protected_one = _tool_round("protected-1", "c" * 10)
    protected_two = _tool_round("protected-2", "d" * 10)
    original = (*first, *second, *protected_one, *protected_two)
    calls: list[tuple[Message, ...]] = []

    def estimate(messages: tuple[Message, ...]) -> int:
        calls.append(messages)
        return len(messages)

    result = ToolReducer(
        keep_recent_tool_rounds=2,
        max_tool_result_chars=100,
        tool_result_head_chars=20,
        tool_result_tail_chars=20,
    ).reduce(
        partition_messages(original),
        current_messages=(),
        initial_estimated_input_tokens=len(original),
        target_tokens=len(original) - len(first),
        estimate=estimate,
    )

    assert result.removed_tool_rounds == 1
    assert result.compacted_tool_results == 0
    assert len(calls) == 1
    assert all(
        "first" not in message.tool_calls
        for message in result.messages
    )
    assert any(
        call.id == "second"
        for message in result.messages
        for call in message.tool_calls
    )


def test_multi_tool_round_is_removed_as_one_protocol_unit() -> None:
    system = Message(role=MessageRole.SYSTEM, content="system")
    multi_round = _tool_round("multi", "x", second_result="y")
    final_answer = Message(role=MessageRole.ASSISTANT, content="最终回答")
    current_user = Message(role=MessageRole.USER, content="继续")
    history = (system, *multi_round, final_answer)
    estimate_calls = 0

    def estimate(messages: tuple[Message, ...]) -> int:
        nonlocal estimate_calls
        estimate_calls += 1
        return len(messages)

    result = ToolReducer(
        keep_recent_tool_rounds=0,
        max_tool_result_chars=100,
        tool_result_head_chars=20,
        tool_result_tail_chars=20,
    ).reduce(
        partition_messages(history),
        current_messages=(current_user,),
        initial_estimated_input_tokens=10,
        target_tokens=3,
        estimate=estimate,
    )

    assert result.messages == (system, final_answer, current_user)
    assert result.removed_tool_rounds == 1
    assert estimate_calls == 1


def test_malformed_and_current_tool_protocols_are_never_modified() -> None:
    expected = ToolCall(id="expected", name="search", arguments={})
    malformed_messages = (
        Message(role=MessageRole.ASSISTANT, tool_calls=(expected,)),
        Message(
            role=MessageRole.TOOL,
            name="search",
            tool_call_id="unexpected",
            content="m" * 500,
        ),
    )
    current_round = _tool_round("current", "c" * 500)
    blocks = partition_messages(malformed_messages)

    assert isinstance(blocks[0], MalformedToolBlock)
    result = ToolReducer(
        keep_recent_tool_rounds=0,
        max_tool_result_chars=100,
        tool_result_head_chars=20,
        tool_result_tail_chars=20,
    ).reduce(
        blocks,
        current_messages=current_round,
        initial_estimated_input_tokens=1_000,
        target_tokens=1,
        estimate=_estimate,
    )

    assert result.messages == (*malformed_messages, *current_round)
    assert result.compacted_tool_results == 0
    assert result.removed_tool_rounds == 0
    assert result.reached_target is False


def _small_window_manager(
    estimator: CharacterEstimator,
    *,
    tool_reducer: ToolReducer | None = None,
) -> ContextManager:
    registry = ModelCapabilityRegistry()
    registry.register_override(
        "test",
        "test-model",
        context_window=1_000,
        max_output_tokens=100,
    )
    settings = ContextSettings(
        _env_file=None,
        context_keep_recent_tool_rounds=2,
        context_preferred_input_tokens=900,
        context_max_tool_result_chars=100,
        context_tool_result_head_chars=20,
        context_tool_result_tail_chars=20,
    )
    return ContextManager(
        estimator=estimator,  # type: ignore[arg-type]
        registry=registry,
        budget_policy=ContextBudgetPolicy(safety_margin_tokens=0),
        context_settings=settings,
        tool_reducer=tool_reducer,
    )


@pytest.mark.asyncio
async def test_context_manager_triggers_then_reestimates_tool_reduction() -> None:
    estimator = CharacterEstimator()
    manager = _small_window_manager(estimator)
    old = _tool_round("old", "h" * 900)
    recent_one = _tool_round("recent-1", "one")
    recent_two = _tool_round("recent-2", "two")
    history = (
        Message(role=MessageRole.SYSTEM, content="system"),
        *old,
        *recent_one,
        *recent_two,
        Message(role=MessageRole.ASSISTANT, content="历史最终回答"),
    )
    current = Message(role=MessageRole.USER, content="当前问题")
    original = (*history, current)

    decision = await manager.prepare(
        original,
        history_count=len(history),
        model="test-model",
        provider="test",
        max_output_tokens=100,
    )

    assert decision.requires_compaction is True
    assert decision.original_estimated_input_tokens == _estimate(original)
    assert decision.prepared_input_tokens == _estimate(decision.messages)
    assert decision.estimated_input_tokens == decision.prepared_input_tokens
    assert decision.prepared_input_tokens < decision.original_estimated_input_tokens
    assert decision.original_usage_ratio is not None
    assert decision.prepared_usage_ratio is not None
    assert decision.prepared_usage_ratio < decision.original_usage_ratio
    assert decision.compaction_stage is (
        ContextCompactionStage.TOOL_RESULTS_AND_ROUNDS
    )
    assert decision.compacted_tool_results == 1
    assert decision.removed_tool_rounds == 1
    assert decision.trimmed is True
    assert decision.reached_target is True
    assert decision.needs_next_compaction_stage is False
    assert len(estimator.requests) >= 2
    assert decision.messages[0] == history[0]
    assert recent_one[0] in decision.messages
    assert recent_one[1] in decision.messages
    assert recent_two[0] in decision.messages
    assert recent_two[1] in decision.messages
    assert history[-1] in decision.messages
    assert decision.messages[-1] is current
    assert original[2].content == "h" * 900


@pytest.mark.asyncio
async def test_below_trigger_preserves_every_message_without_reducer() -> None:
    estimator = CharacterEstimator()
    manager = _small_window_manager(
        estimator,
        tool_reducer=NeverCalledToolReducer(),
    )
    tool_round = _tool_round("short", "完整工具结果")
    history = (
        Message(role=MessageRole.SYSTEM, content="system"),
        Message(role=MessageRole.USER, content="历史问题"),
        *tool_round,
        Message(role=MessageRole.ASSISTANT, content="历史答案"),
    )
    current = Message(role=MessageRole.USER, content="当前问题")
    original = (*history, current)

    decision = await manager.prepare(
        original,
        history_count=len(history),
        model="test-model",
        provider="test",
        max_output_tokens=100,
    )

    assert decision.messages == original
    assert all(
        prepared is source
        for prepared, source in zip(decision.messages, original)
    )
    assert decision.requires_compaction is False
    assert decision.trimmed is False
    assert decision.compaction_stage is ContextCompactionStage.NONE
    assert decision.compacted_tool_results == 0
    assert decision.removed_tool_rounds == 0
    assert decision.reached_target is True
    assert decision.needs_next_compaction_stage is False
    assert len(estimator.requests) == 1


@pytest.mark.asyncio
async def test_tool_layer_exhausted_requests_next_compaction_stage() -> None:
    estimator = CharacterEstimator()
    manager = _small_window_manager(estimator)
    system = Message(role=MessageRole.SYSTEM, content="system")
    long_conversation = Message(role=MessageRole.USER, content="u" * 800)
    current = Message(role=MessageRole.USER, content="当前问题")
    original = (system, long_conversation, current)

    decision = await manager.prepare(
        original,
        history_count=2,
        model="test-model",
        provider="test",
        max_output_tokens=100,
    )

    assert decision.requires_compaction is True
    assert decision.messages == original
    assert decision.trimmed is False
    assert decision.compaction_stage is ContextCompactionStage.NONE
    assert decision.compacted_tool_results == 0
    assert decision.removed_tool_rounds == 0
    assert decision.reached_target is False
    assert decision.needs_next_compaction_stage is True
    assert system in decision.messages
    assert long_conversation in decision.messages
    assert decision.messages[-1] is current


@pytest.mark.asyncio
async def test_context_manager_removes_old_rounds_until_target() -> None:
    estimator = CharacterEstimator()
    manager = _small_window_manager(estimator)
    first = _tool_round("first", "a" * 80)
    second = _tool_round("second", "b" * 80)
    protected_one = _tool_round("protected-1", "c" * 80)
    protected_two = _tool_round("protected-2", "d" * 80)
    conversation = Message(role=MessageRole.USER, content="u" * 200)
    history = (
        conversation,
        *first,
        *second,
        *protected_one,
        *protected_two,
    )
    current = Message(role=MessageRole.USER, content="当前")

    decision = await manager.prepare(
        (*history, current),
        history_count=len(history),
        model="test-model",
        provider="test",
        max_output_tokens=100,
    )

    assert decision.requires_compaction is True
    assert decision.compaction_stage is ContextCompactionStage.TOOL_ROUNDS
    assert decision.compacted_tool_results == 0
    assert decision.removed_tool_rounds == 2
    assert decision.tool_result_tokens_after == _estimate(
        (protected_one[1], protected_two[1])
    )
    assert decision.needs_next_compaction_stage is True
    assert len(estimator.requests) >= 2
    assert conversation in decision.messages
    assert protected_one[0] in decision.messages
    assert protected_one[1] in decision.messages
    assert protected_two[0] in decision.messages
    assert protected_two[1] in decision.messages
    assert decision.messages[-1] is current


@pytest.mark.asyncio
async def test_tool_budget_runs_below_conversation_trigger() -> None:
    estimator = CharacterEstimator()
    registry = ModelCapabilityRegistry()
    registry.register_override(
        "test",
        "test-model",
        context_window=20_000,
        max_output_tokens=1_000,
    )
    settings = ContextSettings(
        _env_file=None,
        context_preferred_input_tokens=10_000,
        context_tool_result_budget_ratio=0.05,
        context_keep_recent_tool_rounds=1,
        context_max_tool_result_chars=100,
        context_tool_result_head_chars=20,
        context_tool_result_tail_chars=20,
    )
    manager = ContextManager(
        estimator=estimator,  # type: ignore[arg-type]
        registry=registry,
        budget_policy=ContextBudgetPolicy(
            safety_margin_tokens=0,
            preferred_input_tokens=10_000,
            tool_result_budget_ratio=0.05,
        ),
        context_settings=settings,
    )
    old = _tool_round("old", "x" * 1_000)
    recent = _tool_round("recent", "当前证据")
    current = Message(role=MessageRole.USER, content="继续")
    original = (*old, *recent, current)

    decision = await manager.prepare(
        original,
        history_count=len(old) + len(recent),
        model="test-model",
        provider="test",
        max_output_tokens=1_000,
    )

    assert decision.original_estimated_input_tokens < decision.trigger_tokens
    assert decision.requires_compaction is True
    assert decision.compacted_tool_results == 1
    assert decision.removed_tool_rounds == 0
    assert decision.tool_result_tokens_after <= (
        decision.tool_result_budget_tokens or 0
    )
    assert recent[1] in decision.messages
    assert original[1].content == "x" * 1_000


@pytest.mark.asyncio
async def test_current_run_old_tool_round_can_be_reduced_but_latest_is_kept() -> None:
    estimator = CharacterEstimator()
    registry = ModelCapabilityRegistry()
    registry.register_override(
        "test",
        "test-model",
        context_window=20_000,
        max_output_tokens=1_000,
    )
    manager = ContextManager(
        estimator=estimator,  # type: ignore[arg-type]
        registry=registry,
        budget_policy=ContextBudgetPolicy(
            safety_margin_tokens=0,
            preferred_input_tokens=10_000,
            tool_result_budget_ratio=0.05,
        ),
        context_settings=ContextSettings(
            _env_file=None,
            context_keep_recent_tool_rounds=1,
            context_max_tool_result_chars=100,
            context_tool_result_head_chars=20,
            context_tool_result_tail_chars=20,
        ),
    )
    historical_user = Message(role=MessageRole.USER, content="开始")
    first_current_round = _tool_round("run-old", "o" * 1_000)
    latest_current_round = _tool_round("run-latest", "最新结果")

    decision = await manager.prepare(
        (historical_user, *first_current_round, *latest_current_round),
        history_count=1,
        model="test-model",
        provider="test",
        max_output_tokens=1_000,
    )

    compacted_old = next(
        message
        for message in decision.messages
        if message.tool_call_id == "run-old"
    )
    assert "tool result compacted" in (compacted_old.content or "")
    assert latest_current_round[1] in decision.messages
