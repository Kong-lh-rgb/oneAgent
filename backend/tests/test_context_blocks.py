"""消息块划分（SystemBlock / ConversationBlock / ToolRoundBlock）测试。"""

from __future__ import annotations

from app.context import (
    BlockType,
    ConversationBlock,
    MalformedToolBlock,
    MessageBlock,
    SystemBlock,
    ToolRoundBlock,
    partition_messages,
)
from app.models.types import Message, MessageRole, ToolCall


def _system(content: str = "sys") -> Message:
    return Message(role=MessageRole.SYSTEM, content=content)


def _user(content: str = "hi") -> Message:
    return Message(role=MessageRole.USER, content=content)


def _assistant(
    content: str | None = "yo",
    tool_calls: tuple[ToolCall, ...] = (),
) -> Message:
    return Message(role=MessageRole.ASSISTANT, content=content, tool_calls=tool_calls)


def _tool(call_id: str, content: str = "result") -> Message:
    return Message(
        role=MessageRole.TOOL,
        tool_call_id=call_id,
        name="web_search",
        content=content,
    )


def _types(blocks: tuple[MessageBlock, ...]) -> list[BlockType]:
    return [block.block_type for block in blocks]


def test_partition_system_and_conversation() -> None:
    messages = (_system("你是助手"), _user("你好"), _assistant("你好！"))

    blocks = partition_messages(messages)

    assert _types(blocks) == [BlockType.SYSTEM, BlockType.CONVERSATION]
    assert isinstance(blocks[0], SystemBlock)
    assert isinstance(blocks[1], ConversationBlock)
    assert blocks[0].messages == (_system("你是助手"),)
    assert blocks[1].messages == (_user("你好"), _assistant("你好！"))


def test_partition_tool_round() -> None:
    call = ToolCall(id="c1", name="web_search", arguments={"query": "AI"})
    messages = (
        _system("sys"),
        _user("搜索"),
        _assistant(tool_calls=(call,)),
        _tool("c1"),
        _assistant("结果如下"),
    )

    blocks = partition_messages(messages)

    assert _types(blocks) == [
        BlockType.SYSTEM,
        BlockType.CONVERSATION,
        BlockType.TOOL_ROUND,
        BlockType.CONVERSATION,
    ]
    assert isinstance(blocks[2], ToolRoundBlock)
    assert blocks[2].messages == (_assistant(tool_calls=(call,)), _tool("c1"))
    assert blocks[3].messages == (_assistant("结果如下"),)


def test_consecutive_system_messages_merge() -> None:
    blocks = partition_messages((_system("a"), _system("b"), _user("x")))

    assert isinstance(blocks[0], SystemBlock)
    assert len(blocks[0]) == 2
    assert _types(blocks) == [BlockType.SYSTEM, BlockType.CONVERSATION]


def test_multiple_tool_rounds_are_separate_blocks() -> None:
    call1 = ToolCall(id="c1", name="t", arguments={})
    call2 = ToolCall(id="c2", name="t", arguments={})
    messages = (
        _assistant(tool_calls=(call1,)),
        _tool("c1"),
        _assistant(tool_calls=(call2,)),
        _tool("c2"),
    )

    blocks = partition_messages(messages)

    assert _types(blocks) == [BlockType.TOOL_ROUND, BlockType.TOOL_ROUND]
    assert len(blocks[0]) == 2
    assert len(blocks[1]) == 2


def test_multi_tool_round_requires_all_matching_results() -> None:
    call1 = ToolCall(id="c1", name="first", arguments={})
    call2 = ToolCall(id="c2", name="second", arguments={})
    messages = (
        _assistant(tool_calls=(call1, call2)),
        _tool("c2"),
        _tool("c1"),
    )

    blocks = partition_messages(messages)

    assert len(blocks) == 1
    assert isinstance(blocks[0], ToolRoundBlock)
    assert blocks[0].messages == messages


def test_mismatched_tool_result_becomes_malformed_block() -> None:
    call = ToolCall(id="expected", name="search", arguments={})
    messages = (_assistant(tool_calls=(call,)), _tool("unexpected"))

    blocks = partition_messages(messages)

    assert len(blocks) == 1
    assert isinstance(blocks[0], MalformedToolBlock)
    assert blocks[0].messages == messages
    assert "exactly match" in blocks[0].reason


def test_incomplete_tool_round_becomes_malformed_block() -> None:
    call = ToolCall(id="missing-result", name="search", arguments={})
    message = _assistant(tool_calls=(call,))

    blocks = partition_messages((message,))

    assert isinstance(blocks[0], MalformedToolBlock)
    assert blocks[0].messages == (message,)


def test_orphan_tool_result_becomes_malformed_block() -> None:
    message = _tool("orphan")

    blocks = partition_messages((_user(), message, _assistant()))

    assert _types(blocks) == [
        BlockType.CONVERSATION,
        BlockType.MALFORMED_TOOL,
        BlockType.CONVERSATION,
    ]
    assert isinstance(blocks[1], MalformedToolBlock)
    assert blocks[1].messages == (message,)


def test_conversation_turns_split_on_new_user() -> None:
    messages = (_user("u1"), _assistant("a1"), _user("u2"), _assistant("a2"))

    blocks = partition_messages(messages)

    assert _types(blocks) == [BlockType.CONVERSATION, BlockType.CONVERSATION]
    assert blocks[0].messages == (_user("u1"), _assistant("a1"))
    assert blocks[1].messages == (_user("u2"), _assistant("a2"))


def test_empty_messages_partition_to_empty() -> None:
    assert partition_messages([]) == ()


def test_partition_preserves_message_order() -> None:
    messages = (
        _system("s"),
        _user("u1"),
        _assistant("a1"),
        _assistant(tool_calls=(ToolCall(id="c1", name="t", arguments={}),)),
        _tool("c1"),
        _user("u2"),
        _assistant("a2"),
    )

    blocks = partition_messages(messages)
    reconstructed = tuple(message for block in blocks for message in block.messages)

    assert reconstructed == messages
