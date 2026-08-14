"""模型能力注册表与 Runtime 模型解析集成测试。"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from pydantic import SecretStr

from app.agent.events import AgentEventType, InMemoryEventHandler
from app.agent.result import AgentStopReason
from app.agent.runtime import AgentRuntime
from app.context import (
    CapabilitySource,
    ContextBudgetPolicy,
    ContextDecision,
    ContextManager,
    ContextSettings,
    ModelCapabilities,
    build_budget_policy,
    build_model_capability_registry,
)
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
from app.tools.base import BaseTool


def _settings(**overrides: object) -> ContextSettings:
    return ContextSettings(_env_file=None, **overrides)


def _default_registry():
    return build_model_capability_registry(
        model_settings=ModelSettings(_env_file=None),
        context_settings=_settings(),
    )


def test_lookup_known_provider_and_model() -> None:
    cap = _default_registry().lookup("qwen", "qwen3.7-plus")

    assert cap.provider == "qwen"
    assert cap.model == "qwen3.7-plus"
    assert cap.context_window == 1_000_000
    assert cap.source is CapabilitySource.BUILTIN


def test_deepseek_builtin_matches_current_official_capacity() -> None:
    cap = _default_registry().lookup("deepseek", "deepseek-v4-flash")

    assert cap.context_window == 1_048_576
    assert cap.max_output_tokens == 393_216


def test_model_capabilities_reject_invalid_limits() -> None:
    with pytest.raises(ValueError, match="context_window"):
        ModelCapabilities(
            provider="qwen",
            model="bad",
            context_window=0,
            max_output_tokens=1,
            source=CapabilitySource.OVERRIDE,
        )


def test_same_provider_different_models_have_different_windows() -> None:
    registry = _default_registry()

    mini = registry.lookup("openai", "gpt-5.4-mini")
    mini4o = registry.lookup("openai", "gpt-4o-mini")

    assert mini.provider == mini4o.provider == "openai"
    assert mini.context_window != mini4o.context_window


def test_user_override_priority() -> None:
    registry = _default_registry()
    registry.register_override(
        "qwen",
        "qwen3.7-plus",
        context_window=50_000,
        max_output_tokens=2_000,
    )

    cap = registry.lookup("qwen", "qwen3.7-plus")

    assert cap.context_window == 50_000
    assert cap.max_output_tokens == 2_000
    assert cap.source is CapabilitySource.OVERRIDE


def test_unknown_model_uses_conservative_fallback(caplog) -> None:
    cap = _default_registry().lookup("mystery", "unknown-model")

    assert cap.context_window == 32_768
    assert cap.max_output_tokens == 4_096
    assert cap.source is CapabilitySource.FALLBACK
    assert "fallback" in caplog.text


# ---- Runtime 模型解析集成 ----


class _ScriptedAdapter(ModelAdapter):
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


class _NoopTool(BaseTool):
    definition = ToolDefinition(name="noop", description="noop")

    async def execute(self, arguments: dict) -> str:
        return "ok"


class _FailingContextManager(ContextManager):
    """模拟上下文准备阶段自身故障。"""

    async def prepare(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolDefinition] = (),
        model: str | None = None,
        provider: str | None = None,
        max_output_tokens: int | None = None,
        history_count: int | None = None,
        summary_state=None,
    ) -> ContextDecision:
        raise RuntimeError("context estimator unavailable")


def _qwen_adapter(responses: Sequence[ModelResponse | Exception]) -> _ScriptedAdapter:
    config = ProviderConfig(
        provider="qwen",
        model="qwen3.7-plus",
        api_key=SecretStr("test-key"),
        api_style=ApiStyle.CHAT_COMPLETIONS,
    )
    return _ScriptedAdapter(config, responses)


def _final_response(content: str = "完成") -> ModelResponse:
    return ModelResponse(
        id="r",
        provider="qwen",
        model="qwen3.7-plus",
        message=Message(role=MessageRole.ASSISTANT, content=content),
    )


def _make_runtime(
    adapter: _ScriptedAdapter,
    *,
    model: str | None = None,
    max_tool_rounds: int | None = None,
    context_manager: ContextManager | None = None,
) -> tuple[AgentRuntime, _ScriptedAdapter]:
    registry = ModelAdapterRegistry(ModelSettings(_env_file=None))
    registry.register(
        "qwen",
        lambda _: adapter,
        config=adapter.config,
        replace=True,
    )
    tools = ToolRegistry()
    tools.register(_NoopTool())
    runtime = AgentRuntime(
        registry,
        tools,
        provider="qwen",
        model=model,
        max_tool_rounds=max_tool_rounds,
        context_manager=context_manager
        or ContextManager(
            registry=_default_registry(),
            budget_policy=build_budget_policy(_settings()),
        ),
    )
    return runtime, adapter


@pytest.mark.asyncio
async def test_runtime_resolves_model_from_adapter_when_self_model_none() -> None:
    runtime, adapter = _make_runtime(_qwen_adapter([_final_response()]))
    handler = InMemoryEventHandler()

    await runtime.run("hi", event_handler=handler)

    started = next(
        event for event in handler.events if event.type is AgentEventType.MODEL_STARTED
    )
    # self._model 为 None → 使用 adapter.default_model
    assert started.model == "qwen3.7-plus"
    assert started.provider == "qwen"
    assert adapter.requests[0].model == "qwen3.7-plus"
    assert adapter.requests[0].model == started.model
    assert (
        adapter.requests[0].max_output_tokens
        == adapter.config.default_max_output_tokens
    )
    assert started.input_budget == 1_000_000 - 4_096 - 4_096


@pytest.mark.asyncio
async def test_context_manager_does_not_modify_messages() -> None:
    manager = ContextManager(
        registry=_default_registry(),
        budget_policy=build_budget_policy(_settings()),
    )
    original = (
        Message(role=MessageRole.USER, content="hi"),
        Message(role=MessageRole.ASSISTANT, content="yo"),
    )

    decision = await manager.prepare(
        original,
        model="qwen3.7-plus",
        provider="qwen",
    )

    assert decision.messages == original
    assert decision.trimmed is False
    assert decision.requires_compaction is False


@pytest.mark.asyncio
async def test_force_final_answer_still_computes_budget() -> None:
    tool_response = ModelResponse(
        id="r1",
        provider="qwen",
        model="qwen3.7-plus",
        message=Message(
            role=MessageRole.ASSISTANT,
            content=None,
            tool_calls=(ToolCall(id="c1", name="noop", arguments={}),),
        ),
    )
    adapter = _qwen_adapter([tool_response, _final_response("done")])
    runtime, adapter = _make_runtime(
        adapter,
        max_tool_rounds=1,
    )
    handler = InMemoryEventHandler()

    await runtime.run("go", event_handler=handler)

    started = [
        event for event in handler.events if event.type is AgentEventType.MODEL_STARTED
    ]
    assert len(started) == 2  # 第一轮正常，第二轮 force_final_answer
    for event in started:
        assert event.context_window == 1_000_000
        assert event.input_budget is not None
        assert event.input_budget > 0
        assert event.working_input_budget == 32_768
        assert event.hard_trigger_tokens is not None
        assert event.hard_target_tokens is not None
        assert event.tool_result_budget_tokens is not None
        assert event.tool_schema_tokens is not None
        assert event.message_tokens_before is not None
        assert event.message_tokens_after is not None
        assert event.unsummarized_conversation_blocks is not None
        assert event.capability_source == CapabilitySource.BUILTIN.value
        assert event.requires_compaction is False


@pytest.mark.asyncio
async def test_runtime_blocks_request_that_exceeds_input_budget() -> None:
    adapter = _qwen_adapter([_final_response("不应调用")])
    registry = _default_registry()
    registry.register_override(
        "qwen",
        "qwen3.7-plus",
        context_window=5_000,
        max_output_tokens=4_096,
    )
    manager = ContextManager(
        registry=registry,
        budget_policy=ContextBudgetPolicy(safety_margin_tokens=0),
    )
    runtime, adapter = _make_runtime(adapter, context_manager=manager)
    handler = InMemoryEventHandler()

    result = await runtime.run("x" * 20_000, event_handler=handler)

    assert result.stop_reason is AgentStopReason.CONTEXT_ERROR
    assert result.error is not None
    assert result.error.type == "ContextWindowExceededError"
    assert adapter.requests == []
    started = next(
        event for event in handler.events if event.type is AgentEventType.MODEL_STARTED
    )
    assert started.exceeds_input_budget is True
    assert started.requires_compaction is True
    assert started.original_estimated_input_tokens is not None
    assert started.prepared_input_tokens == started.estimated_input_tokens
    assert started.original_usage_ratio is not None
    assert started.prepared_usage_ratio is not None
    assert started.compaction_stage == "none"
    assert started.compacted_tool_results == 0
    assert started.removed_tool_rounds == 0
    assert started.reached_target is False
    assert started.needs_next_compaction_stage is True


@pytest.mark.asyncio
async def test_context_preparation_failure_is_not_model_error() -> None:
    adapter = _qwen_adapter([_final_response("不应调用")])
    runtime, adapter = _make_runtime(
        adapter,
        context_manager=_FailingContextManager(),
    )

    result = await runtime.run("hello")

    assert result.stop_reason is AgentStopReason.CONTEXT_ERROR
    assert result.error is not None
    assert result.error.type == "ContextPreparationError"
    assert adapter.requests == []
