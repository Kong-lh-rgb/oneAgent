"""历史工具消息的第一层压缩器。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.models.types import Message, MessageRole

from ..blocks import MessageBlock, ToolRoundBlock

TokenCounter = Callable[[tuple[Message, ...]], int]


@dataclass(frozen=True)
class ToolReductionResult:
    """一次工具层压缩的结果与统计。"""

    messages: tuple[Message, ...]
    estimated_input_tokens: int
    compacted_tool_results: int = 0
    removed_tool_rounds: int = 0
    reached_target: bool = False


class ToolReducer:
    """只压缩历史中已完成且未受保护的 ToolRoundBlock。"""

    def __init__(
        self,
        *,
        keep_recent_tool_rounds: int = 2,
        max_tool_result_chars: int = 8_000,
        tool_result_head_chars: int = 4_000,
        tool_result_tail_chars: int = 2_000,
    ) -> None:
        if keep_recent_tool_rounds < 0:
            raise ValueError("keep_recent_tool_rounds cannot be negative")
        if max_tool_result_chars <= 0:
            raise ValueError("max_tool_result_chars must be greater than zero")
        if tool_result_head_chars < 0 or tool_result_tail_chars < 0:
            raise ValueError("tool result head/tail chars cannot be negative")
        if tool_result_head_chars + tool_result_tail_chars > max_tool_result_chars:
            raise ValueError(
                "tool result head/tail chars cannot exceed max_tool_result_chars"
            )
        self.keep_recent_tool_rounds = keep_recent_tool_rounds
        self.max_tool_result_chars = max_tool_result_chars
        self.tool_result_head_chars = tool_result_head_chars
        self.tool_result_tail_chars = tool_result_tail_chars

    def reduce(
        self,
        history_blocks: Sequence[MessageBlock],
        *,
        current_messages: Sequence[Message],
        initial_estimated_input_tokens: int,
        target_tokens: int,
        estimate: TokenCounter,
        keep_recent_tool_rounds: int | None = None,
    ) -> ToolReductionResult:
        """先缩短旧工具结果，再按最旧优先整体移除工具轮。"""

        keep_recent = (
            self.keep_recent_tool_rounds
            if keep_recent_tool_rounds is None
            else keep_recent_tool_rounds
        )
        if keep_recent < 0:
            raise ValueError("keep_recent_tool_rounds cannot be negative")

        working: list[MessageBlock | None] = list(history_blocks)
        tool_indices = [
            index
            for index, block in enumerate(working)
            if isinstance(block, ToolRoundBlock)
        ]
        protected = set(tool_indices[-keep_recent:]) if keep_recent else set()
        candidates = [index for index in tool_indices if index not in protected]
        estimated = initial_estimated_input_tokens
        compacted_results = 0
        removed_rounds = 0

        if estimated <= target_tokens:
            return self._result(
                working,
                current_messages,
                estimated,
                compacted_results,
                removed_rounds,
                target_tokens,
            )

        for block_index in candidates:
            block = working[block_index]
            if not isinstance(block, ToolRoundBlock):  # pragma: no cover
                continue
            block_messages = list(block.messages)
            for message_index in range(1, len(block_messages)):
                message = block_messages[message_index]
                compacted = self._compact_tool_result(message)
                if compacted == message:
                    continue
                block_messages[message_index] = compacted
                working[block_index] = ToolRoundBlock(tuple(block_messages))
                compacted_results += 1
                prepared_messages = _flatten(working, current_messages)
                estimated = estimate(prepared_messages)
                if estimated <= target_tokens:
                    return ToolReductionResult(
                        messages=prepared_messages,
                        estimated_input_tokens=estimated,
                        compacted_tool_results=compacted_results,
                        removed_tool_rounds=removed_rounds,
                        reached_target=True,
                    )

        for block_index in candidates:
            if working[block_index] is None:  # pragma: no cover
                continue
            working[block_index] = None
            removed_rounds += 1
            prepared_messages = _flatten(working, current_messages)
            estimated = estimate(prepared_messages)
            if estimated <= target_tokens:
                return ToolReductionResult(
                    messages=prepared_messages,
                    estimated_input_tokens=estimated,
                    compacted_tool_results=compacted_results,
                    removed_tool_rounds=removed_rounds,
                    reached_target=True,
                )

        return self._result(
            working,
            current_messages,
            estimated,
            compacted_results,
            removed_rounds,
            target_tokens,
        )

    def _compact_tool_result(self, message: Message) -> Message:
        if message.role is not MessageRole.TOOL:
            return message
        content = message.content or ""
        if len(content) <= self.max_tool_result_chars:
            return message

        head = content[: self.tool_result_head_chars]
        tail = (
            content[-self.tool_result_tail_chars :]
            if self.tool_result_tail_chars
            else ""
        )
        omitted = len(content) - len(head) - len(tail)
        marker = (
            "[tool result compacted: "
            f"tool={message.name or 'unknown'}; "
            f"tool_call_id={message.tool_call_id or 'unknown'}; "
            f"original_chars={len(content)}; omitted {omitted} characters]"
        )
        parts = [part for part in (head, marker, tail) if part]
        compacted_content = "\n".join(parts)
        if len(compacted_content) >= len(content):
            return message
        return message.model_copy(update={"content": compacted_content})

    @staticmethod
    def _result(
        working: Sequence[MessageBlock | None],
        current_messages: Sequence[Message],
        estimated: int,
        compacted_results: int,
        removed_rounds: int,
        target_tokens: int,
    ) -> ToolReductionResult:
        return ToolReductionResult(
            messages=_flatten(working, current_messages),
            estimated_input_tokens=estimated,
            compacted_tool_results=compacted_results,
            removed_tool_rounds=removed_rounds,
            reached_target=estimated <= target_tokens,
        )


def _flatten(
    blocks: Sequence[MessageBlock | None],
    current_messages: Sequence[Message],
) -> tuple[Message, ...]:
    return (
        *(
            message
            for block in blocks
            if block is not None
            for message in block.messages
        ),
        *current_messages,
    )


__all__ = ["ToolReducer", "ToolReductionResult"]
