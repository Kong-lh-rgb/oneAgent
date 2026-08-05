"""模型请求上下文的历史消息整理。"""

from __future__ import annotations

from collections.abc import Sequence

from app.models.types import Message, MessageRole


def compact_model_history(
    messages: Sequence[Message],
    *,
    keep_recent_tool_rounds: int = 0,
) -> tuple[Message, ...]:
    """整理历史消息，分层保留工具结果。

    分层策略：
    - 最近 ``keep_recent_tool_rounds`` 轮工具调用（assistant(tool_calls) 与其
      紧随的 TOOL 结果）完整保留，模型仍能看到最近的工具交互；
    - 更旧的工具轮降级为纯文本：TOOL 结果移除，assistant 若带文本内容则去掉
      tool_calls 保留内容，否则整条移除；
    - SYSTEM 与普通对话消息始终保留。

    原始会话历史不会被修改；这里只用于构造下一次模型请求。默认值 0 表示
    移除全部工具协议（旧行为）。
    """

    msgs = list(messages)
    if keep_recent_tool_rounds <= 0:
        return tuple(
            message
            for message in msgs
            if message.role is not MessageRole.TOOL
            and not (
                message.role is MessageRole.ASSISTANT
                and bool(message.tool_calls)
            )
        )

    # 定位每个工具轮的范围 [assistant_idx, end_idx)，end 指向第一个非 TOOL。
    rounds: list[tuple[int, int]] = []
    index = 0
    count = len(msgs)
    while index < count:
        message = msgs[index]
        if message.role is MessageRole.ASSISTANT and message.tool_calls:
            start = index
            index += 1
            while index < count and msgs[index].role is MessageRole.TOOL:
                index += 1
            rounds.append((start, index))
        else:
            index += 1

    keep_indices: set[int] = set()
    for start, end in rounds[-keep_recent_tool_rounds:]:
        keep_indices.update(range(start, end))

    compacted: list[Message] = []
    for index, message in enumerate(msgs):
        if message.role is MessageRole.TOOL:
            if index in keep_indices:
                compacted.append(message)
            continue
        if message.role is MessageRole.ASSISTANT and message.tool_calls:
            if index in keep_indices:
                compacted.append(message)
            elif message.content:
                # 降级为纯文本对话消息（移除 tool_calls）。
                compacted.append(message.model_copy(update={"tool_calls": ()}))
            continue
        compacted.append(message)
    return tuple(compacted)


__all__ = ["compact_model_history"]
