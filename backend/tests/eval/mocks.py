"""离线 Mock 模型与工具，用于 pytest 验证 Harness 自身（不调用真实模型）。"""

from __future__ import annotations

from collections.abc import Sequence

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
    ToolCall,
)

from .scenario import InitialMessage


class FakeModelAdapter(ModelAdapter):
    """按预设响应序列依次返回的离线模型适配器。"""

    def __init__(
        self,
        config: ProviderConfig,
        responses: Sequence[ModelResponse | Exception],
    ) -> None:
        super().__init__(config)
        self.responses = list(responses)
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def close(self) -> None:
        pass


def model_response(
    *,
    content: str | None = None,
    tool_calls: tuple[ToolCall, ...] = (),
) -> ModelResponse:
    return ModelResponse(
        id="fake-response",
        provider="fake",
        model="fake-model",
        message=Message(
            role=MessageRole.ASSISTANT,
            content=content,
            tool_calls=tool_calls,
        ),
        usage=ModelUsage(input_tokens=10, output_tokens=5, total_tokens=15),
    )


def text_tool_call(
    call_id: str,
    name: str,
    arguments: dict,
) -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=arguments)


def fake_registry(
    responses: Sequence[ModelResponse | Exception],
) -> tuple[ModelAdapterRegistry, FakeModelAdapter]:
    """构造可注入 harness 的 mock 注册表。"""

    config = ProviderConfig(
        provider="fake",
        model="fake-model",
        api_key="offline-test-key",
        api_style=ApiStyle.CHAT_COMPLETIONS,
    )
    adapter = FakeModelAdapter(config, responses)
    registry = ModelAdapterRegistry(ModelSettings(_env_file=None))
    registry.register("fake", lambda _: adapter, config=config)
    return registry, adapter


def history_messages(
    history: tuple[InitialMessage, ...],
) -> tuple[Message, ...]:
    return tuple(
        Message(role=MessageRole(message.role), content=message.content)
        for message in history
    )


__all__ = [
    "FakeModelAdapter",
    "fake_registry",
    "history_messages",
    "model_response",
    "text_tool_call",
]
