"""消息块划分。

把消息序列识别为四类块，供后续压缩使用（以块为最小单元保留/丢弃）：

- ``SystemBlock``：系统提示（压缩时应始终保留）
- ``ConversationBlock``：一轮普通对话（user + 无工具的 assistant）
- ``ToolRoundBlock``：一轮工具调用（assistant(tool_calls) + 紧随的工具结果）
- ``MalformedToolBlock``：未完成、孤立或 ID 不匹配的异常工具协议
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.models.types import Message, MessageRole


class BlockType(StrEnum):
    SYSTEM = "system"
    CONVERSATION = "conversation"
    TOOL_ROUND = "tool_round"
    MALFORMED_TOOL = "malformed_tool"


@dataclass(frozen=True)
class MessageBlock:
    """块基类：一序列消息的不可变分组。"""

    messages: tuple[Message, ...]

    @property
    def block_type(self) -> BlockType:
        raise NotImplementedError

    def __len__(self) -> int:
        return len(self.messages)


@dataclass(frozen=True)
class SystemBlock(MessageBlock):
    """系统提示块。"""

    @property
    def block_type(self) -> BlockType:
        return BlockType.SYSTEM


@dataclass(frozen=True)
class ConversationBlock(MessageBlock):
    """一轮普通对话块（user + 无工具的 assistant）。"""

    @property
    def block_type(self) -> BlockType:
        return BlockType.CONVERSATION


@dataclass(frozen=True)
class ToolRoundBlock(MessageBlock):
    """一轮完整且 ID 对应关系合法的工具调用块。"""

    def __post_init__(self) -> None:
        if not self.messages:
            raise ValueError("ToolRoundBlock messages cannot be empty")
        assistant = self.messages[0]
        if assistant.role is not MessageRole.ASSISTANT or not assistant.tool_calls:
            raise ValueError(
                "ToolRoundBlock must start with an assistant tool call message"
            )

        expected_ids = [call.id for call in assistant.tool_calls]
        if any(not call_id for call_id in expected_ids):
            raise ValueError("ToolCall id cannot be empty")
        if len(set(expected_ids)) != len(expected_ids):
            raise ValueError("ToolCall ids must be unique within one tool round")

        result_messages = self.messages[1:]
        if any(message.role is not MessageRole.TOOL for message in result_messages):
            raise ValueError("ToolRoundBlock may only contain trailing tool results")
        result_ids = [message.tool_call_id for message in result_messages]
        if any(not tool_call_id for tool_call_id in result_ids):
            raise ValueError("ToolResult message requires tool_call_id")
        if Counter(result_ids) != Counter(expected_ids):
            raise ValueError(
                "ToolResult tool_call_id values must exactly match ToolCall ids"
            )

    @property
    def block_type(self) -> BlockType:
        return BlockType.TOOL_ROUND


@dataclass(frozen=True)
class MalformedToolBlock(MessageBlock):
    """未完成或协议异常的工具消息块；压缩时必须保守保留。"""

    reason: str = "malformed tool protocol"

    @property
    def block_type(self) -> BlockType:
        return BlockType.MALFORMED_TOOL


def partition_messages(
    messages: Sequence[Message],
) -> tuple[MessageBlock, ...]:
    """把消息序列划分为块。

    规则：
    - 连续 SYSTEM 消息合并为一个 SystemBlock；
    - assistant(tool_calls) 及其紧随的 TOOL 结果只有在 ID 完整匹配时才构成
      ToolRoundBlock，否则构成 MalformedToolBlock；
    - 孤立 TOOL 消息构成 MalformedToolBlock，不并入普通对话；
    - 其余 user / 无工具 assistant 按轮合并为 ConversationBlock。
    """

    blocks: list[MessageBlock] = []
    conversation: list[Message] = []

    def flush_conversation() -> None:
        nonlocal conversation
        if conversation:
            blocks.append(ConversationBlock(tuple(conversation)))
            conversation = []

    index = 0
    count = len(messages)
    while index < count:
        message = messages[index]
        if message.role is MessageRole.SYSTEM:
            flush_conversation()
            system = [message]
            index += 1
            while index < count and messages[index].role is MessageRole.SYSTEM:
                system.append(messages[index])
                index += 1
            blocks.append(SystemBlock(tuple(system)))
            continue
        if message.role is MessageRole.USER:
            flush_conversation()
            conversation.append(message)
            index += 1
            continue
        if message.role is MessageRole.ASSISTANT and message.tool_calls:
            flush_conversation()
            tool_round = [message]
            index += 1
            while index < count and messages[index].role is MessageRole.TOOL:
                tool_round.append(messages[index])
                index += 1
            try:
                blocks.append(ToolRoundBlock(tuple(tool_round)))
            except ValueError as exc:
                blocks.append(
                    MalformedToolBlock(tuple(tool_round), reason=str(exc))
                )
            continue
        if message.role is MessageRole.TOOL:
            flush_conversation()
            orphan_results = [message]
            index += 1
            while index < count and messages[index].role is MessageRole.TOOL:
                orphan_results.append(messages[index])
                index += 1
            blocks.append(
                MalformedToolBlock(
                    tuple(orphan_results),
                    reason="orphan tool result without assistant tool call",
                )
            )
            continue
        # 无工具的 assistant 并入当前对话轮。
        conversation.append(message)
        index += 1

    flush_conversation()
    return tuple(blocks)


__all__ = [
    "BlockType",
    "ConversationBlock",
    "MalformedToolBlock",
    "MessageBlock",
    "SystemBlock",
    "ToolRoundBlock",
    "partition_messages",
]
