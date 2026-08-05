"""第二层滚动摘要的离线测试。"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from pydantic import SecretStr

from app.context import (
    ContextManager,
    ContextSummarizer,
    ConversationReducer,
    ConversationSummaryState,
    ModelContextSummarizer,
    RollingConversationSummary,
    SQLiteConversationSummaryStore,
    SummaryGenerationResult,
    build_summary_candidate,
)
from app.conversation import SQLiteConversationStore
from app.models.adapter import ModelAdapter
from app.models.config import ModelSettings, ProviderConfig
from app.models.registry import ModelAdapterRegistry
from app.models.types import (
    ApiStyle,
    Message,
    MessageRole,
    ModelRequest,
    ModelResponse,
    ModelUsage,
)


class FakeSummarizer(ContextSummarizer):
    """记录摘要输入并返回固定的短摘要。"""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[
            tuple[RollingConversationSummary | None, tuple[Message, ...]]
        ] = []

    async def summarize(
        self,
        previous_summary: RollingConversationSummary | None,
        messages: Sequence[Message],
    ) -> SummaryGenerationResult:
        self.calls.append((previous_summary, tuple(messages)))
        if self.error is not None:
            raise self.error
        return SummaryGenerationResult(
            summary=RollingConversationSummary(
                current_objective="继续完成当前任务",
                key_decisions=("使用滚动摘要",),
            ),
            usage=ModelUsage(input_tokens=30, output_tokens=10, total_tokens=40),
        )


class SummaryModelAdapter(ModelAdapter):
    """只用于验证模型摘要请求结构的离线适配器。"""

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(
            id="summary-response",
            provider="fake",
            model="fake-model",
            message=Message(
                role=MessageRole.ASSISTANT,
                content=(
                    '{"current_objective":"完成测试","user_constraints":[], '
                    '"key_decisions":[],"completed_work":[],"current_state":[], '
                    '"pending_work":[],"important_facts":[]}'
                ),
            ),
            usage=ModelUsage(input_tokens=11, output_tokens=4, total_tokens=15),
        )

    async def close(self) -> None:
        pass


def _history(rounds: int) -> tuple[Message, ...]:
    messages = [Message(role=MessageRole.SYSTEM, content="主系统提示")]
    for index in range(rounds):
        messages.extend(
            (
                Message(
                    role=MessageRole.USER,
                    content=f"旧问题 {index} " + "问" * 80,
                ),
                Message(
                    role=MessageRole.ASSISTANT,
                    content=f"旧回答 {index} " + "答" * 80,
                ),
            )
        )
    return tuple(messages)


def _estimate(messages: tuple[Message, ...]) -> int:
    return sum(20 + len(message.content or "") for message in messages)


@pytest.mark.asyncio
async def test_context_below_trigger_does_not_call_summarizer() -> None:
    summarizer = FakeSummarizer()
    messages = (
        Message(role=MessageRole.USER, content="短问题"),
        Message(role=MessageRole.ASSISTANT, content="短回答"),
    )

    decision = await ContextManager(
        conversation_reducer=ConversationReducer(summarizer),
    ).prepare(messages, history_count=len(messages))

    assert decision.messages == messages
    assert decision.summary_updated is False
    assert summarizer.calls == []


@pytest.mark.asyncio
async def test_conversation_reducer_summarizes_old_prefix_and_keeps_recent() -> None:
    history = _history(6)
    current = (Message(role=MessageRole.USER, content="当前问题"),)
    prepared = (*history, *current)
    summarizer = FakeSummarizer()

    result = await ConversationReducer(
        summarizer,
        keep_recent_conversation_blocks=2,
        keep_recent_tool_rounds=0,
    ).reduce(
        raw_history=history,
        prepared_messages=prepared,
        current_messages=current,
        previous_state=None,
        initial_estimated_input_tokens=_estimate(prepared),
        target_tokens=700,
        estimate=_estimate,
    )

    assert result.error is None
    assert result.summary_state is not None
    assert result.summary_state.covered_message_count == 9
    assert result.summarized_conversation_blocks == 4
    assert result.summary_usage.total_tokens == 40
    assert result.messages[0] == history[0]
    assert result.messages[-5:] == (*history[-4:], *current)
    assert result.messages[1].name == "oneagent_rolling_summary"
    assert len(summarizer.calls[0][1]) == 8


@pytest.mark.asyncio
async def test_summary_failure_never_removes_original_messages() -> None:
    history = _history(5)
    current = (Message(role=MessageRole.USER, content="继续"),)
    prepared = (*history, *current)

    result = await ConversationReducer(
        FakeSummarizer(error=RuntimeError("模型不可用")),
        keep_recent_conversation_blocks=1,
        keep_recent_tool_rounds=0,
    ).reduce(
        raw_history=history,
        prepared_messages=prepared,
        current_messages=current,
        previous_state=None,
        initial_estimated_input_tokens=_estimate(prepared),
        target_tokens=100,
        estimate=_estimate,
    )

    assert result.messages == prepared
    assert result.summary_state is None
    assert result.error == "RuntimeError: 模型不可用"


@pytest.mark.asyncio
async def test_rolling_summary_uses_previous_summary_and_advances_coverage() -> None:
    history = _history(7)
    previous = ConversationSummaryState(
        summary=RollingConversationSummary(current_objective="旧目标"),
        covered_message_count=5,
    )
    current = (Message(role=MessageRole.USER, content="新输入"),)
    candidate, _ = build_summary_candidate(history, current, previous)
    summarizer = FakeSummarizer()

    result = await ConversationReducer(
        summarizer,
        keep_recent_conversation_blocks=2,
        keep_recent_tool_rounds=0,
    ).reduce(
        raw_history=history,
        prepared_messages=candidate,
        current_messages=current,
        previous_state=previous,
        initial_estimated_input_tokens=_estimate(candidate),
        target_tokens=100,
        estimate=_estimate,
    )

    assert summarizer.calls[0][0] == previous.summary
    assert result.summary_state is not None
    assert result.summary_state.covered_message_count > previous.covered_message_count
    summary_messages = [
        message
        for message in result.messages
        if message.name == "oneagent_rolling_summary"
    ]
    assert len(summary_messages) == 1


@pytest.mark.asyncio
async def test_sqlite_summary_store_round_trip(tmp_path) -> None:
    database_path = tmp_path / "oneagent.db"
    conversation_store = SQLiteConversationStore(database_path)
    await conversation_store.initialize()
    conversation = await conversation_store.create()
    store = SQLiteConversationSummaryStore(database_path)
    await store.initialize()
    state = ConversationSummaryState(
        summary=RollingConversationSummary(
            current_objective="测试持久化",
            pending_work=("继续运行",),
        ),
        covered_message_count=12,
    )

    await store.save(conversation.id, state)

    assert await store.load(conversation.id) == state
    assert await store.delete(conversation.id) is True
    assert await store.load(conversation.id) is None

    await store.save(conversation.id, state)
    await conversation_store.delete(conversation.id)
    assert await store.load(conversation.id) is None


@pytest.mark.asyncio
async def test_model_summarizer_requests_strict_json_without_tools() -> None:
    config = ProviderConfig(
        provider="fake",
        model="fake-model",
        api_key=SecretStr("offline-key"),
        api_style=ApiStyle.CHAT_COMPLETIONS,
    )
    adapter = SummaryModelAdapter(config)
    registry = ModelAdapterRegistry(ModelSettings(_env_file=None))
    registry.register("fake", lambda _: adapter, config=config)
    summarizer = ModelContextSummarizer(registry, provider="fake")

    result = await summarizer.summarize(
        None,
        (Message(role=MessageRole.USER, content="请完成测试"),),
    )

    assert result.summary.current_objective == "完成测试"
    assert result.usage.total_tokens == 15
    assert adapter.requests[0].tools == ()
    assert adapter.requests[0].messages[0].role is MessageRole.SYSTEM
