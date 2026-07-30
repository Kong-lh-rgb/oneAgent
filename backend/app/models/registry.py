"""Lazy model-adapter registry."""

from __future__ import annotations

from collections.abc import Callable

from .adapter import ModelAdapter
from .config import ModelSettings, ProviderConfig
from .errors import UnsupportedProviderError
from .providers import AnthropicAdapter, OpenAICompatibleAdapter
from .types import ModelProvider

AdapterFactory = Callable[[ProviderConfig], ModelAdapter]


class ModelAdapterRegistry:
    """Resolve configured adapters without exposing provider SDKs to callers."""

    def __init__(self, settings: ModelSettings | None = None) -> None:
        self.settings = settings or ModelSettings()
        self._factories: dict[str, AdapterFactory] = {
            ModelProvider.OPENAI.value: OpenAICompatibleAdapter,
            ModelProvider.QWEN.value: OpenAICompatibleAdapter,
            ModelProvider.DEEPSEEK.value: OpenAICompatibleAdapter,
            ModelProvider.ANTHROPIC.value: AnthropicAdapter,
        }
        self._configs: dict[str, ProviderConfig] = {}
        self._instances: dict[str, ModelAdapter] = {}

    def register(
        self,
        provider: str,
        factory: AdapterFactory,
        *,
        config: ProviderConfig | None = None,
        replace: bool = False,
    ) -> None:
        """Register a future provider without changing agent code."""

        name = provider.strip().lower()
        if not name:
            raise ValueError("provider cannot be empty")
        if not replace and name in self._factories:
            raise ValueError(f"Provider '{name}' is already registered.")
        self._factories[name] = factory
        if config is not None:
            self._configs[name] = config
        self._instances.pop(name, None)

    def get(
        self,
        provider: ModelProvider | str | None = None,
    ) -> ModelAdapter:
        name = (
            provider.value
            if isinstance(provider, ModelProvider)
            else (provider or self.settings.model_default_provider.value)
        )
        name = name.strip().lower()

        if name in self._instances:
            return self._instances[name]
        factory = self._factories.get(name)
        if factory is None:
            raise UnsupportedProviderError(name)

        config = self._configs.get(name)
        if config is None:
            try:
                config = self.settings.provider_config(name)
            except ValueError as exc:
                raise UnsupportedProviderError(name) from exc

        adapter = factory(config)
        self._instances[name] = adapter
        return adapter

    async def close(self) -> None:
        for adapter in tuple(self._instances.values()):
            await adapter.close()
        self._instances.clear()
