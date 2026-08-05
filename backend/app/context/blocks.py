"""消息块划分。

把消息序列识别为三类块，供后续压缩使用（以块为最小单元保留/丢弃）：

- ``SystemBlock``：系统提示（压缩时应始终保留）
- ``ConversationBlock``：一轮普通对话（user + 无工具的 assistant）
- ``ToolRoundBlock``：一轮工具调用（assistant(tool_calls) + 紧随的工具结果）
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.models.types import Message, MessageRole


class BlockType(StrEnum):
    SYSTEM = "system"
    CONVERSATION = "conversation"
    TOOL_ROUND = "tool_round"


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
    """一轮工具调用块（assistant(tool_calls) + 紧随的工具结果）。"""

    @property
    def block_type(self) -> BlockType:
        return BlockType.TOOL_ROUND


def partition_messages(
    messages: Sequence[Message],
) -> tuple[MessageBlock, ...]:
    """把消息序列划分为块。

    规则：
    - 连续 SYSTEM 消息合并为一个 SystemBlock；
    - assistant(tool_calls) 及其紧随的 TOOL 结果构成一个 ToolRoundBlock；
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
            blocks.append(ToolRoundBlock(tuple(tool_round)))
            continue
        # 无工具的 assistant 或孤立 TOOL：并入当前对话轮
        conversation.append(message)
        index += 1

    flush_conversation()
    return tuple(blocks)


__all__ = [
    "BlockType",
    "ConversationBlock",
    "MessageBlock",
    "SystemBlock",
    "ToolRoundBlock",
    "partition_messages",
]
