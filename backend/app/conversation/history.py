"""会话消息历史的压缩与整理。"""

from __future__ import annotations

from app.models.types import Message, MessageRole


def compact_conversation_history(
    messages: tuple[Message, ...] | list[Message],
) -> list[Message]:
    """移除历史工具协议消息，只保留跨轮对话真正需要的内容。

    完整工具过程已经保存在 Trace 和 AgentResult 中；把原始工具输出继续放入
    下一轮模型请求会重复计费，并让长网页正文持续占用上下文。
    """

    return [
        message
        for message in messages
        if message.role is not MessageRole.TOOL
        and not (
            message.role is MessageRole.ASSISTANT
            and bool(message.tool_calls)
        )
    ]


__all__ = ["compact_conversation_history"]
