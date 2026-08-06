"""摘要请求关闭 reasoning 的离线测试。

针对 deepseek（reasoning 模型）：
- 摘要请求默认携带 extra_body={"thinking":{"type":"disabled"}}；
- 其他 Provider（qwen / 未知）默认不携带，避免误伤；
- disable_reasoning 显式参数可强制覆盖。
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.context import ModelContextSummarizer
from app.models.adapter import ModelAdapter
from app.models.config import ModelSettings, ProviderConfig
from app.models.registry import ModelAdapterRegistry
from app.models.types import (
    ApiStyle,
    Message,
    MessageRole,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelUsage,
)

_VALID_SUMMARY_JSON = (
    '{"current_objective":"完成测试","user_constraints":[],"key_decisions":[],'
    '"completed_work":[],"current_state":[],"pending_work":[],"important_facts":[]}'
)


class RecordingAdapter(ModelAdapter):
    """记录收到的请求，固定返回合法摘要 JSON。"""

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(
            id="summary-response",
            provider=self.provider,
            model=self.default_model,
            message=Message(
                role=MessageRole.ASSISTANT,
                content=_VALID_SUMMARY_JSON,
            ),
            usage=ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2),
        )

    async def close(self) -> None:
        pass


def _registry_and_adapter(
    provider: str,
) -> tuple[ModelAdapterRegistry, RecordingAdapter]:
    config = ProviderConfig(
        provider=provider,
        model="summary-model",
        api_key=SecretStr("offline-key"),
        api_style=ApiStyle.CHAT_COMPLETIONS,
    )
    adapter = RecordingAdapter(config)
    registry = ModelAdapterRegistry(ModelSettings(_env_file=None))
    registry.register(
        provider,
        lambda _: adapter,
        config=config,
        replace=True,
    )
    return registry, adapter


async def _run_summary(
    registry: ModelAdapterRegistry,
    *,
    provider: ModelProvider | str,
    disable_reasoning: bool | None = None,
) -> None:
    kwargs: dict = {"provider": provider}
    if disable_reasoning is not None:
        kwargs["disable_reasoning"] = disable_reasoning
    summarizer = ModelContextSummarizer(registry, **kwargs)
    await summarizer.summarize(
        None,
        (Message(role=MessageRole.USER, content="请压缩"),),
    )


@pytest.mark.asyncio
async def test_deepseek_disables_reasoning_by_default() -> None:
    registry, adapter = _registry_and_adapter(ModelProvider.DEEPSEEK.value)

    await _run_summary(registry, provider=ModelProvider.DEEPSEEK)

    assert adapter.requests[0].extra_body == {"thinking": {"type": "disabled"}}


@pytest.mark.asyncio
async def test_qwen_keeps_reasoning_by_default() -> None:
    registry, adapter = _registry_and_adapter(ModelProvider.QWEN.value)

    await _run_summary(registry, provider=ModelProvider.QWEN)

    assert adapter.requests[0].extra_body == {}


@pytest.mark.asyncio
async def test_unknown_provider_keeps_reasoning_by_default() -> None:
    registry, adapter = _registry_and_adapter("fake")

    await _run_summary(registry, provider="fake")

    assert adapter.requests[0].extra_body == {}


@pytest.mark.asyncio
async def test_disable_flag_false_overrides_deepseek() -> None:
    registry, adapter = _registry_and_adapter(ModelProvider.DEEPSEEK.value)

    await _run_summary(
        registry,
        provider=ModelProvider.DEEPSEEK,
        disable_reasoning=False,
    )

    assert adapter.requests[0].extra_body == {}


@pytest.mark.asyncio
async def test_disable_flag_true_overrides_qwen() -> None:
    registry, adapter = _registry_and_adapter(ModelProvider.QWEN.value)

    await _run_summary(
        registry,
        provider=ModelProvider.QWEN,
        disable_reasoning=True,
    )

    assert adapter.requests[0].extra_body == {"thinking": {"type": "disabled"}}


@pytest.mark.asyncio
async def test_summary_request_keeps_schema_and_max_tokens() -> None:
    registry, adapter = _registry_and_adapter(ModelProvider.DEEPSEEK.value)

    await _run_summary(registry, provider=ModelProvider.DEEPSEEK)

    request = adapter.requests[0]
    assert request.tools == ()
    assert request.max_output_tokens == 1_024
    assert request.messages[0].role is MessageRole.SYSTEM
