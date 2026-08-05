"""基于滚动摘要的普通对话第二层压缩器。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from app.models.types import Message, MessageRole, ModelUsage

from ..blocks import (
    ConversationBlock,
    MalformedToolBlock,
    MessageBlock,
    ToolRoundBlock,
    partition_messages,
)
from ..summarizer import ContextSummarizer
from ..summary import (
    SUMMARY_MESSAGE_NAME,
    ConversationSummaryState,
)

TokenCounter = Callable[[tuple[Message, ...]], int]


@dataclass(frozen=True)
class ConversationReductionResult:
    """第二层滚动摘要的请求上下文、状态和统计。"""

    messages: tuple[Message, ...]
    estimated_input_tokens: int
    summary_state: ConversationSummaryState | None = None
    summarized_conversation_blocks: int = 0
    summary_usage: ModelUsage = field(default_factory=ModelUsage)
    reached_target: bool = False
    error: str | None = None


@dataclass(frozen=True)
class _IndexedBlock:
    block: MessageBlock
    start: int
    end: int


class ConversationReducer:
    """用一份滚动摘要替代较早的普通对话历史。"""

    def __init__(
        self,
        summarizer: ContextSummarizer,
        *,
        keep_recent_conversation_blocks: int = 4,
        keep_recent_tool_rounds: int = 2,
    ) -> None:
        if keep_recent_conversation_blocks < 0:
            raise ValueError("keep_recent_conversation_blocks cannot be negative")
        if keep_recent_tool_rounds < 0:
            raise ValueError("keep_recent_tool_rounds cannot be negative")
        self._summarizer = summarizer
        self.keep_recent_conversation_blocks = keep_recent_conversation_blocks
        self.keep_recent_tool_rounds = keep_recent_tool_rounds

    async def reduce(
        self,
        *,
        raw_history: Sequence[Message],
        prepared_messages: Sequence[Message],
        current_messages: Sequence[Message],
        previous_state: ConversationSummaryState | None,
        initial_estimated_input_tokens: int,
        target_tokens: int,
        estimate: TokenCounter,
    ) -> ConversationReductionResult:
        """摘要可覆盖的最旧前缀；失败时原样返回 prepared_messages。"""

        raw = tuple(raw_history)
        prepared = tuple(prepared_messages)
        covered = previous_state.covered_message_count if previous_state else 0
        if covered > len(raw):
            return self._unchanged(
                prepared,
                previous_state,
                initial_estimated_input_tokens,
                target_tokens,
                "summary covered_message_count exceeds current history length",
            )

        indexed = _index_blocks(raw[covered:], offset=covered)
        cutoff = _summary_cutoff(
            indexed,
            keep_recent_conversation_blocks=self.keep_recent_conversation_blocks,
            keep_recent_tool_rounds=self.keep_recent_tool_rounds,
            history_length=len(raw),
        )
        summary_blocks = [
            item
            for item in indexed
            if item.end <= cutoff and isinstance(item.block, ConversationBlock)
        ]
        if cutoff <= covered or not summary_blocks:
            return self._unchanged(
                prepared,
                previous_state,
                initial_estimated_input_tokens,
                target_tokens,
            )

        source_messages = tuple(
            message for item in summary_blocks for message in item.block.messages
        )
        try:
            generated = await self._summarizer.summarize(
                previous_state.summary if previous_state else None,
                source_messages,
            )
        except Exception as exc:
            return self._unchanged(
                prepared,
                previous_state,
                initial_estimated_input_tokens,
                target_tokens,
                f"{type(exc).__name__}: {exc}",
            )

        new_state = ConversationSummaryState(
            summary=generated.summary,
            covered_message_count=cutoff,
        )
        reduced_messages = _replace_covered_prefix(
            prepared,
            raw[covered:cutoff],
            new_state,
        )
        estimated = estimate(reduced_messages)
        if estimated >= initial_estimated_input_tokens:
            return ConversationReductionResult(
                messages=prepared,
                estimated_input_tokens=initial_estimated_input_tokens,
                summary_state=previous_state,
                summary_usage=generated.usage,
                reached_target=initial_estimated_input_tokens <= target_tokens,
                error="generated summary did not reduce the request context",
            )
        return ConversationReductionResult(
            messages=reduced_messages,
            estimated_input_tokens=estimated,
            summary_state=new_state,
            summarized_conversation_blocks=len(summary_blocks),
            summary_usage=generated.usage,
            reached_target=estimated <= target_tokens,
        )

    @staticmethod
    def _unchanged(
        messages: tuple[Message, ...],
        state: ConversationSummaryState | None,
        estimated: int,
        target_tokens: int,
        error: str | None = None,
    ) -> ConversationReductionResult:
        return ConversationReductionResult(
            messages=messages,
            estimated_input_tokens=estimated,
            summary_state=state,
            reached_target=estimated <= target_tokens,
            error=error,
        )


def build_summary_candidate(
    raw_history: Sequence[Message],
    current_messages: Sequence[Message],
    state: ConversationSummaryState | None,
) -> tuple[tuple[Message, ...], int]:
    """用已持久化摘要替换其覆盖的原始历史前缀。"""

    history = tuple(raw_history)
    current = tuple(current_messages)
    if state is None or state.covered_message_count == 0:
        return (*history, *current), len(history)
    if state.covered_message_count > len(history):
        return (*history, *current), len(history)

    covered_prefix = history[: state.covered_message_count]
    preserved_system = tuple(
        message
        for message in covered_prefix
        if message.role is MessageRole.SYSTEM and message.name != SUMMARY_MESSAGE_NAME
    )
    prepared_history = (
        *preserved_system,
        state.summary.to_message(),
        *history[state.covered_message_count :],
    )
    return (*prepared_history, *current), len(prepared_history)


def _index_blocks(
    messages: Sequence[Message],
    *,
    offset: int,
) -> tuple[_IndexedBlock, ...]:
    indexed: list[_IndexedBlock] = []
    position = offset
    for block in partition_messages(messages):
        end = position + len(block.messages)
        indexed.append(_IndexedBlock(block=block, start=position, end=end))
        position = end
    return tuple(indexed)


def _summary_cutoff(
    blocks: Sequence[_IndexedBlock],
    *,
    keep_recent_conversation_blocks: int,
    keep_recent_tool_rounds: int,
    history_length: int,
) -> int:
    conversation_indices = [
        index
        for index, item in enumerate(blocks)
        if isinstance(item.block, ConversationBlock)
    ]
    tool_indices = [
        index
        for index, item in enumerate(blocks)
        if isinstance(item.block, ToolRoundBlock)
    ]
    protected: set[int] = {
        index
        for index, item in enumerate(blocks)
        if isinstance(item.block, MalformedToolBlock)
    }
    if keep_recent_conversation_blocks:
        protected.update(conversation_indices[-keep_recent_conversation_blocks:])
    if keep_recent_tool_rounds:
        protected.update(tool_indices[-keep_recent_tool_rounds:])
    if not protected:
        return history_length
    return min(blocks[index].start for index in protected)


def _replace_covered_prefix(
    prepared_messages: Sequence[Message],
    covered_raw_messages: Sequence[Message],
    state: ConversationSummaryState,
) -> tuple[Message, ...]:
    removable_ids = {
        id(message)
        for message in covered_raw_messages
        if message.role is not MessageRole.SYSTEM
    }
    covered_call_ids = {
        call.id for message in covered_raw_messages for call in message.tool_calls
    }
    kept: list[Message] = []
    for message in prepared_messages:
        if message.name == SUMMARY_MESSAGE_NAME:
            continue
        if id(message) in removable_ids:
            continue
        if message.tool_call_id in covered_call_ids:
            continue
        if message.tool_calls and all(
            call.id in covered_call_ids for call in message.tool_calls
        ):
            continue
        kept.append(message)

    insertion_index = 0
    while (
        insertion_index < len(kept) and kept[insertion_index].role is MessageRole.SYSTEM
    ):
        insertion_index += 1
    kept.insert(insertion_index, state.summary.to_message())
    return tuple(kept)


__all__ = [
    "ConversationReducer",
    "ConversationReductionResult",
    "build_summary_candidate",
]
