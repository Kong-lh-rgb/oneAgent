"""上下文配置、模型能力注册表与 ContextManager 集成测试。"""

from __future__ import annotations

import pytest

from app.context import (
    CapabilitySource,
    ContextManager,
    ContextSettings,
    build_budget_policy,
    build_model_capability_registry,
)
from app.models.config import ModelSettings
from app.models.types import Message, MessageRole, ModelProvider


def _settings(**overrides: object) -> ContextSettings:
    return ContextSettings(_env_file=None, **overrides)


def _qwen_model_settings() -> ModelSettings:
    return ModelSettings(
        _env_file=None,
        DASHSCOPE_API_KEY="qwen-key",
        model_default_provider=ModelProvider.QWEN,
    )


def test_context_settings_defaults() -> None:
    settings = _settings()

    assert settings.context_window_qwen == 1_000_000
    assert settings.context_trigger_ratio == 0.80
    assert settings.context_target_ratio == 0.60
    assert settings.context_preferred_input_tokens == 32_768
    assert settings.context_working_trigger_ratio == 0.70
    assert settings.context_working_target_ratio == 0.45
    assert settings.context_tool_result_budget_ratio == 0.35
    assert settings.context_safety_margin_tokens == 4_096
    assert settings.context_keep_recent_tool_rounds == 2
    assert settings.context_keep_recent_conversation_blocks == 4
    assert settings.context_max_unsummarized_conversation_blocks == 30
    assert settings.context_summary_max_output_tokens == 1_024
    assert settings.context_max_tool_result_chars == 8_000
    assert settings.context_tool_result_head_chars == 4_000
    assert settings.context_tool_result_tail_chars == 2_000


def test_tool_result_segment_config_must_fit_maximum() -> None:
    with pytest.raises(ValueError, match="head/tail"):
        _settings(
            context_max_tool_result_chars=100,
            context_tool_result_head_chars=80,
            context_tool_result_tail_chars=30,
        )


def test_builtin_lookup_uses_exact_model() -> None:
    registry = build_model_capability_registry(
        model_settings=_qwen_model_settings(),
        context_settings=_settings(),
    )

    cap = registry.lookup("qwen", "qwen3.7-plus")

    assert cap.context_window == 1_000_000
    assert cap.source is CapabilitySource.BUILTIN


def test_provider_default_window_reads_settings() -> None:
    registry = build_model_capability_registry(
        model_settings=_qwen_model_settings(),
        context_settings=_settings(context_window_qwen=200_000),
    )

    cap = registry.lookup("qwen", "some-other-qwen-model")

    assert cap.context_window == 200_000
    assert cap.source is CapabilitySource.PROVIDER_DEFAULT


def test_override_from_settings_applies_to_current_model() -> None:
    registry = build_model_capability_registry(
        model_settings=_qwen_model_settings(),
        context_settings=_settings(
            context_window_override=99_999,
            max_output_tokens_override=5_000,
        ),
    )

    cap = registry.lookup("qwen", "qwen3.7-plus")

    assert cap.context_window == 99_999
    assert cap.max_output_tokens == 5_000
    assert cap.source is CapabilitySource.OVERRIDE


def test_override_with_explicit_provider_model() -> None:
    registry = build_model_capability_registry(
        model_settings=_qwen_model_settings(),
        context_settings=_settings(
            context_override_provider="qwen",
            context_override_model="qwen3.7-plus",
            context_window_override=66_000,
        ),
    )

    cap = registry.lookup("qwen", "qwen3.7-plus")

    assert cap.context_window == 66_000
    assert cap.source is CapabilitySource.OVERRIDE


def test_explicit_override_does_not_require_provider_api_key() -> None:
    registry = build_model_capability_registry(
        model_settings=ModelSettings(_env_file=None),
        context_settings=_settings(
            context_override_provider="qwen",
            context_override_model="qwen3.7-plus",
            context_window_override=55_555,
        ),
    )

    cap = registry.lookup("qwen", "qwen3.7-plus")

    assert cap.context_window == 55_555
    assert cap.source is CapabilitySource.OVERRIDE


def test_zero_max_output_override_is_rejected() -> None:
    with pytest.raises(ValueError, match="greater than 0"):
        _settings(max_output_tokens_override=0)


@pytest.mark.asyncio
async def test_context_manager_decision_fields() -> None:
    settings = _settings()
    registry = build_model_capability_registry(
        model_settings=_qwen_model_settings(),
        context_settings=settings,
    )
    manager = ContextManager(
        registry=registry,
        budget_policy=build_budget_policy(settings),
    )
    messages = (Message(role=MessageRole.USER, content="hello"),)

    decision = await manager.prepare(
        messages,
        model="qwen3.7-plus",
        provider="qwen",
    )

    assert decision.provider == "qwen"
    assert decision.model == "qwen3.7-plus"
    assert decision.context_window == 1_000_000
    assert decision.input_budget == 1_000_000 - 65_536 - 4_096
    assert decision.working_input_budget == 32_768
    assert decision.hard_trigger_tokens == int(decision.input_budget * 0.8)
    assert decision.hard_target_tokens == int(decision.input_budget * 0.6)
    assert decision.trigger_tokens == int(32_768 * 0.7)
    assert decision.target_tokens == int(32_768 * 0.45)
    assert decision.tool_result_budget_tokens == int(decision.target_tokens * 0.35)
    assert decision.usage_ratio is not None
    assert decision.requires_compaction is False
    assert decision.capability_source == CapabilitySource.BUILTIN.value
    assert decision.trimmed is False
    assert decision.messages == messages
    assert "original_estimated=" in (decision.reason or "")
