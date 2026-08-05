"""模型请求上下文的历史消息整理。"""

from __future__ import annotations

from collections.abc import Sequence

from app.models.types import Message, MessageRole

from .blocks import (
    MalformedToolBlock,
    MessageBlock,
    ToolRoundBlock,
    partition_messages,
)


def compact_model_blocks(
    blocks: Sequence[MessageBlock],
    *,
    keep_recent_tool_rounds: int = 0,
) -> tuple[Message, ...]:
    """按消息块整理历史，并返回模型请求消息。"""

    if keep_recent_tool_rounds < 0:
        raise ValueError("keep_recent_tool_rounds cannot be negative")

    tool_rounds = [block for block in blocks if isinstance(block, ToolRoundBlock)]
    retained_round_ids = {
        id(block) for block in tool_rounds[-keep_recent_tool_rounds:]
    } if keep_recent_tool_rounds else set()

    compacted: list[Message] = []
    for block in blocks:
        if isinstance(block, MalformedToolBlock):
            compacted.extend(block.messages)
            continue
        if isinstance(block, ToolRoundBlock):
            if id(block) in retained_round_ids:
                compacted.extend(block.messages)
                continue

            assistant_message = block.messages[0]
            if assistant_message.content:
                compacted.append(
                    assistant_message.model_copy(update={"tool_calls": ()})
                )
            continue

        # 防御性移除不属于合法 ToolRoundBlock 的孤立工具结果。
        compacted.extend(
            message
            for message in block.messages
            if message.role is not MessageRole.TOOL
        )
    return tuple(compacted)


def compact_model_history(
    messages: Sequence[Message],
    *,
    keep_recent_tool_rounds: int = 0,
) -> tuple[Message, ...]:
    """直接整理历史消息，供兼容调用和独立测试使用。

    分层策略：
    - 最近 ``keep_recent_tool_rounds`` 轮工具调用（assistant(tool_calls) 与其
      紧随的 TOOL 结果）完整保留，模型仍能看到最近的工具交互；
    - 更旧的工具轮降级为纯文本：TOOL 结果移除，assistant 若带文本内容则去掉
      tool_calls 保留内容，否则整条移除；
    - SYSTEM 与普通对话消息始终保留。

    原始会话历史不会被修改。ContextManager 的主流程不会无条件调用本函数，
    而是在达到压缩触发线后使用 ToolReducer。默认值 0 保持该辅助函数的旧行为。
    """

    return compact_model_blocks(
        partition_messages(messages),
        keep_recent_tool_rounds=keep_recent_tool_rounds,
    )


__all__ = ["compact_model_blocks", "compact_model_history"]
