from __future__ import annotations

from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import SecretStr

from app.application import select_provider
from app.models import (
    ApiStyle,
    Message,
    MessageRole,
    ModelProvider,
    ModelRequest,
    ModelSettings,
    ProviderConfig,
    ProviderNotConfiguredError,
    ToolCall,
    ToolDefinition,
)
from app.models.providers import AnthropicAdapter, OpenAICompatibleAdapter


class AsyncRecorder:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.kwargs: dict[str, Any] | None = None

    async def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return self.response


class AsyncStream:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def __aiter__(self):
        self._iterator = iter(self._items)
        return self

    async def __anext__(self) -> Any:
        try:
            return next(self._iterator)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class FakeAnthropicMessageStream:
    def __init__(self, deltas: list[str], final_message: Any) -> None:
        self.text_stream = AsyncStream(deltas)
        self.final_message = final_message

    async def __aenter__(self) -> FakeAnthropicMessageStream:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def get_final_message(self) -> Any:
        return self.final_message


class FakeOpenAIClient:
    def __init__(
        self,
        *,
        responses_result: Any | None = None,
        chat_result: Any | None = None,
    ) -> None:
        self.responses = AsyncRecorder(responses_result)
        self.chat = SimpleNamespace(completions=AsyncRecorder(chat_result))
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class FakeAnthropicClient:
    def __init__(self, response: Any) -> None:
        self.messages = AsyncRecorder(response)
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class FakeStreamingAnthropicClient:
    def __init__(self, stream: FakeAnthropicMessageStream) -> None:
        self.messages = SimpleNamespace(stream=lambda **_: stream)

    async def close(self) -> None:
        pass


def capture_deltas(target: list[str]) -> Callable[[str], Awaitable[None]]:
    async def capture(delta: str) -> None:
        target.append(delta)

    return capture


def provider_config(
    provider: str,
    api_style: ApiStyle,
    model: str,
) -> ProviderConfig:
    return ProviderConfig(
        provider=provider,
        model=model,
        api_key=SecretStr("test-key"),
        api_style=api_style,
    )


@pytest.mark.asyncio
async def test_openai_responses_adapter_normalizes_tool_calls() -> None:
    result = SimpleNamespace(
        id="resp_1",
        model="gpt-test",
        output_text="",
        output=[
            SimpleNamespace(
                type="function_call",
                call_id="call_1",
                name="weather",
                arguments='{"city":"Shanghai"}',
            )
        ],
        status="completed",
        usage=SimpleNamespace(
            input_tokens=10,
            output_tokens=4,
            total_tokens=14,
        ),
    )
    client = FakeOpenAIClient(responses_result=result)
    adapter = OpenAICompatibleAdapter(
        provider_config("openai", ApiStyle.RESPONSES, "gpt-test"),
        client=client,
    )

    response = await adapter.complete(
        ModelRequest(
            messages=(Message(role=MessageRole.USER, content="Weather?"),),
            tools=(
                ToolDefinition(
                    name="weather",
                    parameters={
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                    },
                ),
            ),
        )
    )

    assert response.provider == "openai"
    assert response.finish_reason == "tool_calls"
    assert response.message.tool_calls[0].arguments == {"city": "Shanghai"}
    assert client.responses.kwargs["tools"][0]["name"] == "weather"


@pytest.mark.asyncio
async def test_openai_responses_stream_emits_text_and_returns_final_response() -> None:
    final = SimpleNamespace(
        id="resp_stream",
        model="gpt-test",
        output_text="你好",
        output=[],
        status="completed",
        usage=SimpleNamespace(input_tokens=4, output_tokens=2, total_tokens=6),
    )
    stream = AsyncStream(
        [
            SimpleNamespace(type="response.output_text.delta", delta="你"),
            SimpleNamespace(type="response.output_text.delta", delta="好"),
            SimpleNamespace(type="response.completed", response=final),
        ]
    )
    adapter = OpenAICompatibleAdapter(
        provider_config("openai", ApiStyle.RESPONSES, "gpt-test"),
        client=FakeOpenAIClient(responses_result=stream),
    )
    deltas: list[str] = []

    response = await adapter.complete_stream(
        ModelRequest(messages=(Message(role=MessageRole.USER, content="hello"),)),
        on_text_delta=capture_deltas(deltas),
    )

    assert deltas == ["你", "好"]
    assert response.message.content == "你好"
    assert response.usage.total_tokens == 6


@pytest.mark.asyncio
async def test_openai_compatible_chat_adapter_preserves_tool_history() -> None:
    result = SimpleNamespace(
        id="chat_1",
        model="deepseek-test",
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(
                    content="done",
                    tool_calls=None,
                    reasoning_content="先分析用户意图",
                ),
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=8,
            completion_tokens=2,
            total_tokens=10,
        ),
    )
    client = FakeOpenAIClient(chat_result=result)
    adapter = OpenAICompatibleAdapter(
        provider_config(
            "deepseek",
            ApiStyle.CHAT_COMPLETIONS,
            "deepseek-test",
        ),
        client=client,
    )

    response = await adapter.complete(
        ModelRequest(
            messages=(
                Message(
                    role=MessageRole.ASSISTANT,
                    tool_calls=(
                        ToolCall(
                            id="call_1",
                            name="lookup",
                            arguments={"id": 7},
                        ),
                    ),
                ),
                Message(
                    role=MessageRole.TOOL,
                    tool_call_id="call_1",
                    content="record",
                ),
            )
        )
    )

    sent = client.chat.completions.kwargs["messages"]
    assert sent[0]["tool_calls"][0]["function"]["arguments"] == '{"id":7}'
    assert sent[1]["tool_call_id"] == "call_1"
    assert response.message.content == "done"
    assert response.message.reasoning == "先分析用户意图"


@pytest.mark.asyncio
async def test_openai_chat_stream_rebuilds_text_and_tool_calls() -> None:
    chunks = AsyncStream(
        [
            SimpleNamespace(
                id="chat-stream",
                model="deepseek-test",
                usage=None,
                choices=[
                    SimpleNamespace(
                        finish_reason=None,
                        delta=SimpleNamespace(
                            content="先",
                            tool_calls=None,
                            reasoning_content="思考中",
                        ),
                    )
                ],
            ),
            SimpleNamespace(
                id="chat-stream",
                model="deepseek-test",
                usage=None,
                choices=[
                    SimpleNamespace(
                        finish_reason="tool_calls",
                        delta=SimpleNamespace(
                            content=None,
                            tool_calls=[
                                SimpleNamespace(
                                    index=0,
                                    id="call-1",
                                    function=SimpleNamespace(
                                        name="search",
                                        arguments='{"query":"Vesta"}',
                                    ),
                                )
                            ],
                        ),
                    )
                ],
            ),
            SimpleNamespace(
                id="chat-stream",
                model="deepseek-test",
                usage=SimpleNamespace(
                    prompt_tokens=5,
                    completion_tokens=3,
                    total_tokens=8,
                ),
                choices=[],
            ),
        ]
    )
    adapter = OpenAICompatibleAdapter(
        provider_config("deepseek", ApiStyle.CHAT_COMPLETIONS, "deepseek-test"),
        client=FakeOpenAIClient(chat_result=chunks),
    )
    deltas: list[str] = []

    response = await adapter.complete_stream(
        ModelRequest(messages=(Message(role=MessageRole.USER, content="search"),)),
        on_text_delta=capture_deltas(deltas),
    )

    assert deltas == ["先"]
    assert response.message.content == "先"
    assert response.message.tool_calls[0].arguments == {"query": "Vesta"}
    assert response.message.reasoning == "思考中"
    assert response.usage.total_tokens == 8


@pytest.mark.asyncio
async def test_anthropic_adapter_separates_system_and_tool_messages() -> None:
    result = SimpleNamespace(
        id="msg_1",
        model="claude-test",
        content=[
            SimpleNamespace(type="text", text="Calling "),
            SimpleNamespace(
                type="tool_use",
                id="tool_1",
                name="search",
                input={"query": "Vesta"},
            ),
        ],
        stop_reason="tool_use",
        usage=SimpleNamespace(input_tokens=12, output_tokens=5),
    )
    client = FakeAnthropicClient(result)
    adapter = AnthropicAdapter(
        provider_config(
            "anthropic",
            ApiStyle.ANTHROPIC_MESSAGES,
            "claude-test",
        ),
        client=client,
    )

    response = await adapter.complete(
        ModelRequest(
            messages=(
                Message(role=MessageRole.SYSTEM, content="Be concise."),
                Message(role=MessageRole.USER, content="Search."),
            )
        )
    )

    assert client.messages.kwargs["system"] == "Be concise."
    assert client.messages.kwargs["messages"] == [
        {"role": "user", "content": "Search."}
    ]
    assert response.message.content == "Calling "
    assert response.message.tool_calls[0].name == "search"
    assert response.usage.total_tokens == 17


@pytest.mark.asyncio
async def test_anthropic_stream_emits_deltas_and_returns_complete_message() -> None:
    final = SimpleNamespace(
        id="msg-stream",
        model="claude-test",
        content=[SimpleNamespace(type="text", text="完成")],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=6, output_tokens=2),
    )
    adapter = AnthropicAdapter(
        provider_config("anthropic", ApiStyle.ANTHROPIC_MESSAGES, "claude-test"),
        client=FakeStreamingAnthropicClient(
            FakeAnthropicMessageStream(["完", "成"], final)
        ),
    )
    deltas: list[str] = []

    response = await adapter.complete_stream(
        ModelRequest(messages=(Message(role=MessageRole.USER, content="do it"),)),
        on_text_delta=capture_deltas(deltas),
    )

    assert deltas == ["完", "成"]
    assert response.message.content == "完成"
    assert response.usage.total_tokens == 8


def test_settings_are_lazy_and_accept_dashscope_key_alias() -> None:
    settings = ModelSettings(
        _env_file=None,
        DASHSCOPE_API_KEY="qwen-key",
    )

    assert settings.configured_providers() == (ModelProvider.QWEN,)
    assert settings.provider_config(ModelProvider.QWEN).api_key_value() == "qwen-key"
    with pytest.raises(ProviderNotConfiguredError):
        settings.provider_config(ModelProvider.OPENAI)


def test_chat_auto_selects_the_only_configured_provider() -> None:
    settings = ModelSettings(
        _env_file=None,
        DASHSCOPE_API_KEY="qwen-key",
    )

    assert select_provider(settings, None) is ModelProvider.QWEN
