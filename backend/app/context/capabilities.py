"""模型能力定义与注册表。

提供按 ``(provider, model)`` 查询模型能力（上下文窗口、默认最大输出 token）
的注册表。查找优先级：

    用户覆盖 > 内置精确模型 > Provider 默认能力 > 保守兜底

内置配置为手工维护的默认值，不是从 Provider API 查询的；未知模型使用
保守兜底（如 32K），并记录 warning，不导致 Agent 崩溃。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from enum import StrEnum

from app.models.config import ModelSettings
from app.models.types import ModelProvider

from .config import ContextSettings

logger = logging.getLogger("vesta.context.capabilities")


class CapabilitySource(StrEnum):
    """模型能力来源。"""

    OVERRIDE = "override"           # 用户显式覆盖
    BUILTIN = "builtin"             # 内置精确模型配置
    PROVIDER_DEFAULT = "provider_default"  # Provider 默认能力
    FALLBACK = "fallback"           # 未知模型保守兜底


@dataclass(frozen=True)
class ModelCapabilities:
    """一个具体模型的上下文能力。"""

    provider: str
    model: str
    context_window: int
    max_output_tokens: int
    source: CapabilitySource

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ValueError("provider cannot be empty")
        if not self.model.strip():
            raise ValueError("model cannot be empty")
        if self.context_window <= 0:
            raise ValueError("context_window must be greater than zero")
        if self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be greater than zero")


# 内置精确模型：(provider, model) -> (context_window, max_output_tokens)
# 与 ModelSettings 默认支持的模型对齐；同 provider 不同模型可拥有不同窗口。
_BUILTIN_MODELS: dict[tuple[str, str], tuple[int, int]] = {
    ("openai", "gpt-5.4-mini"): (200_000, 16_384),
    ("openai", "gpt-4o-mini"): (128_000, 16_384),
    ("qwen", "qwen3.7-plus"): (1_000_000, 65_536),
    ("deepseek", "deepseek-v4-flash"): (1_048_576, 393_216),
    ("anthropic", "claude-sonnet-4-6"): (200_000, 16_384),
}

# Provider 默认能力（内置模型之外的同 provider 模型）的最大输出
_PROVIDER_DEFAULT_MAX_OUTPUT: dict[str, int] = {
    "openai": 16_384,
    "qwen": 65_536,
    "deepseek": 393_216,
    "anthropic": 16_384,
}

FALLBACK_CONTEXT_WINDOW = 32_768  # 未知模型的保守兜底窗口（32K）
FALLBACK_MAX_OUTPUT_TOKENS = 4_096


class ModelCapabilityRegistry:
    """按 (provider, model) 查询模型能力。"""

    def __init__(
        self,
        *,
        builtin: dict[tuple[str, str], ModelCapabilities] | None = None,
        provider_defaults: dict[str, ModelCapabilities] | None = None,
        fallback: ModelCapabilities | None = None,
    ) -> None:
        self._overrides: dict[tuple[str, str], ModelCapabilities] = {}
        self._builtin = {**(builtin or {})}
        self._provider_defaults = {**(provider_defaults or {})}
        self._fallback = fallback or ModelCapabilities(
            provider="*",
            model="*",
            context_window=FALLBACK_CONTEXT_WINDOW,
            max_output_tokens=FALLBACK_MAX_OUTPUT_TOKENS,
            source=CapabilitySource.FALLBACK,
        )

    def register_override(
        self,
        provider: str,
        model: str,
        *,
        context_window: int | None = None,
        max_output_tokens: int | None = None,
    ) -> ModelCapabilities:
        """为用户覆盖当前模型的能力；未提供的字段沿用既有值。"""

        base = self.lookup(provider, model)
        capabilities = ModelCapabilities(
            provider=provider,
            model=model,
            context_window=(
                context_window
                if context_window is not None
                else base.context_window
            ),
            max_output_tokens=(
                max_output_tokens
                if max_output_tokens is not None
                else base.max_output_tokens
            ),
            source=CapabilitySource.OVERRIDE,
        )
        self._overrides[(provider, model)] = capabilities
        return capabilities

    def lookup(self, provider: str | None, model: str | None) -> ModelCapabilities:
        """查询 (provider, model) 的模型能力；未知模型回退到保守兜底。"""

        key = (provider, model)
        if key in self._overrides:
            return self._overrides[key]
        if key in self._builtin:
            return self._builtin[key]
        default = self._provider_defaults.get(provider or "")
        if default is not None:
            return replace(
                default,
                provider=provider or "*",
                model=model or "*",
            )
        logger.warning(
            "Unknown model capability; using conservative fallback "
            "provider=%s model=%s",
            provider,
            model,
        )
        return replace(
            self._fallback,
            provider=provider or "*",
            model=model or "*",
        )


def build_model_capability_registry(
    model_settings: ModelSettings | None = None,
    context_settings: ContextSettings | None = None,
) -> ModelCapabilityRegistry:
    """从配置构建注册表：内置精确模型 + Provider 默认 + 用户覆盖。"""

    settings = context_settings or ContextSettings()
    builtin: dict[tuple[str, str], ModelCapabilities] = {}
    for (provider, model), (window, max_output) in _BUILTIN_MODELS.items():
        builtin[(provider, model)] = ModelCapabilities(
            provider=provider,
            model=model,
            context_window=window,
            max_output_tokens=max_output,
            source=CapabilitySource.BUILTIN,
        )
    provider_defaults = {
        name: ModelCapabilities(
            provider=name,
            model="*",
            context_window=_window_for(name, settings),
            max_output_tokens=_PROVIDER_DEFAULT_MAX_OUTPUT.get(name, 4_096),
            source=CapabilitySource.PROVIDER_DEFAULT,
        )
        for name in _PROVIDER_DEFAULT_MAX_OUTPUT
    }
    registry = ModelCapabilityRegistry(
        builtin=builtin,
        provider_defaults=provider_defaults,
    )

    target = _resolve_override_target(model_settings, settings)
    if target is not None and (
        settings.context_window_override is not None
        or settings.max_output_tokens_override is not None
    ):
        provider, model = target
        registry.register_override(
            provider,
            model,
            context_window=settings.context_window_override,
            max_output_tokens=settings.max_output_tokens_override,
        )
    return registry


def _window_for(name: str, settings: ContextSettings) -> int:
    if name == "openai":
        return settings.context_window_openai
    if name == "qwen":
        return settings.context_window_qwen
    if name == "deepseek":
        return settings.context_window_deepseek
    if name == "anthropic":
        return settings.context_window_anthropic
    return settings.context_window_default


def _resolve_override_target(
    model_settings: ModelSettings | None,
    settings: ContextSettings,
) -> tuple[str, str] | None:
    """确定覆盖作用的目标模型；未显式指定时作用于当前配置的默认模型。"""

    resolved_settings = model_settings or ModelSettings()
    try:
        provider = (
            ModelProvider(settings.context_override_provider)
            if settings.context_override_provider
            else resolved_settings.model_default_provider
        )
    except ValueError:
        return None
    model = settings.context_override_model or str(
        getattr(resolved_settings, f"{provider.value}_model")
    )
    return provider.value, model


__all__ = [
    "CapabilitySource",
    "FALLBACK_CONTEXT_WINDOW",
    "ModelCapabilities",
    "ModelCapabilityRegistry",
    "build_model_capability_registry",
]
