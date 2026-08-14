"""历史工具消息的第一层压缩器。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.models.types import Message, MessageRole

from ..blocks import MessageBlock, ToolRoundBlock, partition_messages

TokenCounter = Callable[[tuple[Message, ...]], int]


@dataclass(frozen=True)
class ToolReductionResult:
    """一次工具层压缩的结果与统计。"""

    messages: tuple[Message, ...]
    estimated_input_tokens: int
    compacted_tool_results: int = 0
    removed_tool_rounds: int = 0
    reached_target: bool = False
    tool_result_tokens_before: int = 0
    tool_result_tokens_after: int = 0
    tool_result_budget_tokens: int | None = None


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

    def project(
        self,
        messages: Sequence[Message],
        *,
        tool_result_budget_tokens: int,
        estimate_request: TokenCounter,
        estimate_tool_results: TokenCounter,
        keep_recent_tool_rounds: int | None = None,
    ) -> ToolReductionResult:
        """每轮把所有已完成工具结果整理到独立预算内。

        最近若干工具轮作为当前工作证据保持完整；其余工具轮先截短结果，
        预算仍不足时再按最旧优先成组移除，绝不破坏工具调用协议。
        """

        if tool_result_budget_tokens < 0:
            raise ValueError("tool_result_budget_tokens cannot be negative")
        keep_recent = (
            self.keep_recent_tool_rounds
            if keep_recent_tool_rounds is None
            else keep_recent_tool_rounds
        )
        if keep_recent < 0:
            raise ValueError("keep_recent_tool_rounds cannot be negative")

        working: list[MessageBlock | None] = list(partition_messages(messages))
        tool_indices = [
            index
            for index, block in enumerate(working)
            if isinstance(block, ToolRoundBlock)
        ]
        protected = set(tool_indices[-keep_recent:]) if keep_recent else set()
        candidates = [index for index in tool_indices if index not in protected]
        before = _estimate_tool_messages(working, estimate_tool_results)
        after = before
        compacted_results = 0
        removed_rounds = 0

        if after <= tool_result_budget_tokens:
            prepared = _flatten(working, ())
            return ToolReductionResult(
                messages=prepared,
                estimated_input_tokens=estimate_request(prepared),
                reached_target=True,
                tool_result_tokens_before=before,
                tool_result_tokens_after=after,
                tool_result_budget_tokens=tool_result_budget_tokens,
            )

        # 只要工具结果总量超限，所有超长结果都先做确定性截短；最近轮只免于
        # 整轮删除，不免于单条输出上限，避免一个新结果撑爆整次请求。
        for block_index in tool_indices:
            block = working[block_index]
            if not isinstance(block, ToolRoundBlock):  # pragma: no cover
                continue
            block_messages = list(block.messages)
            for message_index in range(1, len(block_messages)):
                compacted = self._compact_tool_result(block_messages[message_index])
                if compacted == block_messages[message_index]:
                    continue
                block_messages[message_index] = compacted
                working[block_index] = ToolRoundBlock(tuple(block_messages))
                compacted_results += 1
                after = _estimate_tool_messages(working, estimate_tool_results)
                if after <= tool_result_budget_tokens:
                    return self._projection_result(
                        working,
                        estimate_request,
                        before=before,
                        after=after,
                        budget=tool_result_budget_tokens,
                        compacted_results=compacted_results,
                        removed_rounds=removed_rounds,
                    )

        for block_index in candidates:
            if working[block_index] is None:  # pragma: no cover
                continue
            working[block_index] = None
            removed_rounds += 1
            after = _estimate_tool_messages(working, estimate_tool_results)
            if after <= tool_result_budget_tokens:
                break
        return self._projection_result(
            working,
            estimate_request,
            before=before,
            after=after,
            budget=tool_result_budget_tokens,
            compacted_results=compacted_results,
            removed_rounds=removed_rounds,
        )

    @staticmethod
    def _projection_result(
        working: Sequence[MessageBlock | None],
        estimate_request: TokenCounter,
        *,
        before: int,
        after: int,
        budget: int,
        compacted_results: int,
        removed_rounds: int,
    ) -> ToolReductionResult:
        messages = _flatten(working, ())
        return ToolReductionResult(
            messages=messages,
            estimated_input_tokens=estimate_request(messages),
            compacted_tool_results=compacted_results,
            removed_tool_rounds=removed_rounds,
            reached_target=after <= budget,
            tool_result_tokens_before=before,
            tool_result_tokens_after=after,
            tool_result_budget_tokens=budget,
        )

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


def _estimate_tool_messages(
    blocks: Sequence[MessageBlock | None],
    estimate: TokenCounter,
) -> int:
    tool_messages = tuple(
        message
        for block in blocks
        if isinstance(block, ToolRoundBlock)
        for message in block.messages[1:]
    )
    return estimate(tool_messages)
