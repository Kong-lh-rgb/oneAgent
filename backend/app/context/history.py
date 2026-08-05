"""模型请求上下文的历史消息整理。"""

from __future__ import annotations

from collections.abc import Sequence

from app.models.types import Message, MessageRole


def compact_model_history(messages: Sequence[Message]) -> tuple[Message, ...]:
    """移除已完成轮次中的工具协议消息。

    工具调用与工具结果仍保存在原始会话历史中。这个函数只用于构造下一次
    模型请求，避免旧工具输出在后续用户轮次中被反复发送和计费。
    """

    return tuple(
        message
        for message in messages
        if message.role is not MessageRole.TOOL
        and not (
            message.role is MessageRole.ASSISTANT
            and bool(message.tool_calls)
        )
    )


__all__ = ["compact_model_history"]
