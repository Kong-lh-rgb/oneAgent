"""token 估算器与 Runtime 模型调用前估算测试。"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.agent.events import AgentEventType, InMemoryEventHandler
from app.agent.runtime import AgentRuntime
from app.context import ContextDecision, ContextManager, TokenEstimator
from app.models.adapter import ModelAdapter
from app.models.config import ModelSettings, ProviderConfig
from app.models.registry import ModelAdapterRegistry
from app.models.types import (
    ApiStyle,
    Message,
    MessageRole,
    ModelRequest,
    ModelResponse,
    ToolCall,
    ToolDefinition,
)
from app.tools import ToolRegistry


def test_estimate_text_returns_positive_count() -> None:
    estimator = TokenEstimator()
    assert estimator.estimate_text("hello world") > 0


def test_estimate_empty_messages_is_zero() -> None:
    estimator = TokenEstimator()
    assert estimator.estimate_messages([]) == 0


def test_estimate_messages_counts_content_and_role() -> None:
    estimator = TokenEstimator()
    messages = (Message(role=MessageRole.USER, content="hello world"),)
    assert estimator.estimate_messages(messages) > estimator.estimate_text(
        "hello world"
    )


def test_estimate_messages_counts_tool_calls() -> None:
    estimator = TokenEstimator()
    plain = (Message(role=MessageRole.USER, content="hello"),)
    with_tool = (
        Message(
            role=MessageRole.ASSISTANT,
            content="searching",
            tool_calls=(
                ToolCall(
                    id="call-1",
                    name="web_search",
                    arguments={"query": "Vesta"},
                ),
            ),
        ),
    )
    assert estimator.estimate_messages(with_tool) > estimator.estimate_messages(plain)


def test_estimate_tools_counts_definition() -> None:
    estimator = TokenEstimator()
    tool = ToolDefinition(
        name="web_search",
        description="search the web",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
        },
    )
    assert estimator.estimate_tools((tool,)) > 0


def test_estimate_request_sums_messages_and_tools() -> None:
    estimator = TokenEstimator()
    messages = (Message(role=MessageRole.USER, content="hi"),)
    tool = ToolDefinition(name="foo", description="bar")
    request_tokens = estimator.estimate_request(messages, tools=(tool,))
    assert request_tokens >= estimator.estimate_messages(messages)
    assert request_tokens >= estimator.estimate_tools((tool,))


def test_default_token_estimator_is_shared() -> None:
    from app.context import default_token_estimator

    assert default_token_estimator() is default_token_estimator()


def test_openai_model_has_no_conservative_factor() -> None:
    estimator = TokenEstimator()
    assert estimator.factor_for("openai", "gpt-4o") == 1.0
    assert estimator.factor_for(None, "gpt-5.4-mini") == 1.0


def test_non_openai_models_get_conservative_factor() -> None:
    estimator = TokenEstimator()
    assert estimator.factor_for("qwen", "qwen3.7-plus") > 1.0
    assert estimator.factor_for("deepseek", "deepseek-v4-flash") > 1.0
    assert estimator.factor_for("anthropic", "claude-sonnet-4-6") > 1.0
    assert estimator.factor_for(None, "unknown-model") > 1.0


def test_conservative_factor_inflates_non_openai_estimate() -> None:
    estimator = TokenEstimator()
    raw_estimator = TokenEstimator(factors={"qwen": 1.0, "other": 1.0})

    inflated = estimator.estimate_text(
        "hello world",
        model="qwen3.7-plus",
        provider="qwen",
    )
    raw = raw_estimator.estimate_text(
        "hello world",
        model="qwen3.7-plus",
        provider="qwen",
    )

    assert inflated > raw


def test_custom_factors_override_defaults() -> None:
    estimator = TokenEstimator(factors={"qwen": 1.5})
    assert estimator.factor_for("qwen", "qwen3.7-plus") == 1.5


class _FakeAdapter(ModelAdapter):
    def __init__(self, config: ProviderConfig, content: str) -> None:
        super().__init__(config)
        self._content = content
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(
            id="r",
            provider=self.provider,
            model=self.default_model,
            message=Message(role=MessageRole.ASSISTANT, content=self._content),
        )

    async def close(self) -> None:
        pass


def _fake_runtime(
    content: str = "完成",
    *,
    context_manager: ContextManager | None = None,
) -> tuple[AgentRuntime, _FakeAdapter]:
    config = ProviderConfig(
        provider="fake",
        model="fake-model",
        api_key=SecretStr("test-key"),
        api_style=ApiStyle.CHAT_COMPLETIONS,
    )
    adapter = _FakeAdapter(config, content)
    registry = ModelAdapterRegistry(ModelSettings(_env_file=None))
    registry.register("fake", lambda _: adapter, config=config)
    return (
        AgentRuntime(
            registry,
            ToolRegistry(),
            provider="fake",
            context_manager=context_manager,
        ),
        adapter,
    )


@pytest.mark.asyncio
async def test_runtime_emits_estimated_input_tokens_before_model_call() -> None:
    runtime, adapter = _fake_runtime(context_manager=ContextManager())
    handler = InMemoryEventHandler()

    await runtime.run("hello world", event_handler=handler)

    started = next(
        event for event in handler.events if event.type is AgentEventType.MODEL_STARTED
    )
    assert started.estimated_input_tokens is not None
    assert started.estimated_input_tokens > 0
    assert started.context_window is not None
    assert started.context_window > 0
    assert started.input_budget is not None
    assert started.input_budget > 0
    assert started.step == 1


class _EmptyContextManager(ContextManager):
    async def prepare(
        self,
        messages,
        *,
        tools=(),
        model=None,
        provider=None,
        max_output_tokens=None,
        history_count=None,
        summary_state=None,
    ):
        return ContextDecision(
            messages=tuple(messages),
            tools=tuple(tools),
            estimated_input_tokens=None,
            trimmed=False,
        )


@pytest.mark.asyncio
async def test_runtime_respects_context_manager_decision() -> None:
    runtime, _ = _fake_runtime(context_manager=_EmptyContextManager())
    handler = InMemoryEventHandler()

    await runtime.run("hi", event_handler=handler)

    started = next(
        event for event in handler.events if event.type is AgentEventType.MODEL_STARTED
    )
    assert started.estimated_input_tokens is None
    assert started.context_trimmed is False


@pytest.mark.asyncio
async def test_context_manager_returns_messages_unchanged_and_estimates() -> None:
    manager = ContextManager(TokenEstimator())
    messages = (Message(role=MessageRole.USER, content="hello"),)

    decision = await manager.prepare(
        messages,
        tools=(),
        model="qwen3.7-plus",
        provider="qwen",
    )

    assert decision.messages == messages
    assert decision.tools == ()
    assert decision.trimmed is False
    assert decision.estimated_input_tokens is not None
    assert decision.estimated_input_tokens > 0
