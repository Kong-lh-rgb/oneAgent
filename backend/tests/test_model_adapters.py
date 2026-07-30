from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import SecretStr

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
from app.models.chat import _select_provider
from app.models.providers import AnthropicAdapter, OpenAICompatibleAdapter


class AsyncRecorder:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.kwargs: dict[str, Any] | None = None

    async def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return self.response


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
async def test_openai_compatible_chat_adapter_preserves_tool_history() -> None:
    result = SimpleNamespace(
        id="chat_1",
        model="deepseek-test",
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content="done", tool_calls=None),
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
                input={"query": "OneAgent"},
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

    assert _select_provider(settings, None) is ModelProvider.QWEN
