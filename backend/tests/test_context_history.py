"""历史消息分层保留策略测试。"""

from __future__ import annotations

import pytest

from app.context import ContextManager, compact_model_history
from app.models.types import Message, MessageRole, ToolCall


def _call(call_id: str, query: str) -> ToolCall:
    return ToolCall(id=call_id, name="web_search", arguments={"query": query})


def _tool_result(call: ToolCall, content: str) -> Message:
    return Message(
        role=MessageRole.TOOL,
        tool_call_id=call.id,
        name=call.name,
        content=content,
    )


def _three_round_history() -> tuple[Message, ...]:
    """三段连续工具轮：问题1/2/3，各自带一个工具调用与结果。"""
    calls = [_call("c1", "q1"), _call("c2", "q2"), _call("c3", "q3")]
    return (
        Message(role=MessageRole.USER, content="问题1"),
        Message(role=MessageRole.ASSISTANT, tool_calls=(calls[0],)),
        _tool_result(calls[0], "结果1"),
        Message(role=MessageRole.USER, content="问题2"),
        Message(role=MessageRole.ASSISTANT, tool_calls=(calls[1],)),
        _tool_result(calls[1], "结果2"),
        Message(role=MessageRole.USER, content="问题3"),
        Message(role=MessageRole.ASSISTANT, tool_calls=(calls[2],)),
        _tool_result(calls[2], "结果3"),
    )


def test_default_removes_all_tool_protocol() -> None:
    """回归：keep_recent_tool_rounds=0 时仍移除全部工具协议。"""

    call = _call("c1", "q1")
    messages = (
        Message(role=MessageRole.USER, content="搜索"),
        Message(role=MessageRole.ASSISTANT, tool_calls=(call,)),
        _tool_result(call, "结果1"),
        Message(role=MessageRole.ASSISTANT, content="答案1"),
    )

    compacted = compact_model_history(messages)

    assert [message.role for message in compacted] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]
    assert compacted[-1].content == "答案1"


def test_keep_recent_one_preserves_latest_round_only() -> None:
    history = _three_round_history()

    compacted = compact_model_history(history, keep_recent_tool_rounds=1)

    roles = [message.role for message in compacted]
    assert roles == [
        MessageRole.USER,  # 问题1
        MessageRole.USER,  # 问题2（c1 轮被移除）
        MessageRole.USER,  # 问题3（c2 轮被移除）
        MessageRole.ASSISTANT,  # c3 轮完整保留
        MessageRole.TOOL,
    ]
    assert compacted[-2].tool_calls == (_call("c3", "q3"),)
    assert compacted[-1].tool_call_id == "c3"


def test_keep_recent_two_preserves_two_latest_rounds() -> None:
    history = _three_round_history()

    compacted = compact_model_history(history, keep_recent_tool_rounds=2)

    roles = [message.role for message in compacted]
    assert roles == [
        MessageRole.USER,  # 问题1
        MessageRole.USER,  # 问题2（c1 轮被移除）
        MessageRole.ASSISTANT,  # c2 轮完整保留
        MessageRole.TOOL,
        MessageRole.USER,  # 问题3
        MessageRole.ASSISTANT,  # c3 轮完整保留
        MessageRole.TOOL,
    ]
    assert compacted[2].tool_calls == (_call("c2", "q2"),)
    assert compacted[5].tool_calls == (_call("c3", "q3"),)


def test_old_round_with_text_degrades_to_plain_text() -> None:
    """旧轮 assistant 带文本时降级为纯文本并保留，去掉 tool_calls。"""

    call1 = _call("c1", "q1")
    call2 = _call("c2", "q2")
    messages = (
        Message(role=MessageRole.USER, content="问题1"),
        Message(
            role=MessageRole.ASSISTANT,
            content="先查一下",
            tool_calls=(call1,),
        ),
        _tool_result(call1, "结果1"),
        Message(role=MessageRole.USER, content="问题2"),
        Message(role=MessageRole.ASSISTANT, tool_calls=(call2,)),
        _tool_result(call2, "结果2"),
    )

    compacted = compact_model_history(messages, keep_recent_tool_rounds=1)

    assert compacted[0] == messages[0]
    assert compacted[1].role is MessageRole.ASSISTANT
    assert compacted[1].content == "先查一下"
    assert compacted[1].tool_calls == ()
    assert compacted[2] == messages[3]  # 问题2
    assert compacted[3].tool_calls == (call2,)
    assert compacted[4] == _tool_result(call2, "结果2")


def test_keep_more_than_available_keeps_all_rounds() -> None:
    history = _three_round_history()

    compacted = compact_model_history(history, keep_recent_tool_rounds=99)

    assert compacted == history


def test_orphan_tool_message_is_conservatively_preserved() -> None:
    """异常工具协议不能被历史整理误删。"""

    call = _call("c1", "q1")
    messages = (
        Message(role=MessageRole.USER, content="问题"),
        _tool_result(call, "孤立结果"),
    )

    compacted = compact_model_history(messages, keep_recent_tool_rounds=5)

    assert compacted == messages


@pytest.mark.asyncio
async def test_context_manager_below_trigger_does_not_run_history_compaction() -> None:
    manager = ContextManager()
    old_call = _call("old", "旧查询")
    recent_call = _call("recent", "新查询")
    history = (
        Message(role=MessageRole.USER, content="上一轮"),
        Message(role=MessageRole.ASSISTANT, tool_calls=(old_call,)),
        _tool_result(old_call, "旧结果"),
        Message(role=MessageRole.USER, content="这一轮"),
        Message(role=MessageRole.ASSISTANT, tool_calls=(recent_call,)),
        _tool_result(recent_call, "新结果"),
    )

    decision = await manager.prepare(
        history,
        history_count=len(history),
        model="qwen3.7-plus",
        provider="qwen",
    )

    assert manager.keep_recent_tool_rounds == 2
    assert decision.messages == history
    assert decision.trimmed is False
    assert decision.requires_compaction is False
    assert decision.compacted_tool_results == 0
    assert decision.removed_tool_rounds == 0
