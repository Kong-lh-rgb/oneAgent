"""设置中心V2的读取、保存、装配与连接测试。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from pydantic import SecretStr

from app.memory import MemoryMaintenanceConfig, MemoryReflectionConfig
from app.models.config import ModelSettings, ProviderConfig
from app.models.providers import AnthropicAdapter, OpenAICompatibleAdapter
from app.models.types import Message, MessageRole, ModelProvider, ModelRequest

from .models import (
    ModelRoleSettings,
    ModelSettingsUpdate,
    ProviderSettings,
    ProviderSettingsUpdate,
    StoredModelSettings,
)
from .secrets import MacOSKeychainSecretStore, ModelSecretStore
from .store import ModelSettingsStore

_PROVIDER_LABELS = {
    ModelProvider.OPENAI: "OpenAI",
    ModelProvider.QWEN: "Qwen",
    ModelProvider.DEEPSEEK: "DeepSeek",
    ModelProvider.ANTHROPIC: "Claude",
}
_KEY_FIELDS = {
    ModelProvider.OPENAI: "openai_api_key",
    ModelProvider.QWEN: "qwen_api_key",
    ModelProvider.DEEPSEEK: "deepseek_api_key",
    ModelProvider.ANTHROPIC: "anthropic_api_key",
}
_OFFICIAL_TEST_HOSTS = {
    ModelProvider.OPENAI: "api.openai.com",
    ModelProvider.QWEN: "dashscope.aliyuncs.com",
    ModelProvider.DEEPSEEK: "api.deepseek.com",
    ModelProvider.ANTHROPIC: "api.anthropic.com",
}


@dataclass(frozen=True)
class EffectiveModelConfiguration:
    settings: ModelSettings
    reflection: MemoryReflectionConfig | None
    maintenance: MemoryMaintenanceConfig | None


class ModelSettingsService:
    def __init__(
        self,
        *,
        store: ModelSettingsStore | None = None,
        secrets: ModelSecretStore | None = None,
        base_settings: ModelSettings | None = None,
    ) -> None:
        self.store = store or ModelSettingsStore()
        self.secrets = secrets or MacOSKeychainSecretStore()
        self.base_settings = base_settings or ModelSettings()

    def view(self, *, active_provider: str, active_model: str) -> dict[str, Any]:
        base = self.base_settings
        stored = self.store.load() or _defaults(base)
        providers = []
        for provider in ModelProvider:
            item = stored.providers[provider.value]
            keychain_key = self.secrets.get(provider.value)
            env_key = _secret_value(getattr(base, _KEY_FIELDS[provider]))
            source = (
                "keychain"
                if keychain_key
                else "environment"
                if env_key
                else "none"
            )
            providers.append(
                {
                    **item.model_dump(mode="json"),
                    "label": _PROVIDER_LABELS[provider],
                    "configured": source != "none",
                    "key_source": source,
                }
            )
        return {
            "default_provider": stored.default_provider.value,
            "providers": providers,
            "reflection": stored.reflection.model_dump(mode="json"),
            "maintenance": stored.maintenance.model_dump(mode="json"),
            "active_provider": active_provider,
            "active_model": active_model,
            "restart_required": (
                stored.default_provider.value != active_provider
                or stored.providers[stored.default_provider.value].model != active_model
            ),
        }

    def save(self, update: ModelSettingsUpdate) -> StoredModelSettings:
        base = self.base_settings
        existing_keys = {
            provider: bool(self.secrets.get(provider.value))
            or bool(_secret_value(getattr(base, _KEY_FIELDS[provider])))
            for provider in ModelProvider
        }
        submitted = {item.provider: item for item in update.providers}
        default_item = submitted[update.default_provider]
        if not default_item.api_key and not existing_keys[update.default_provider]:
            raise ValueError("default provider requires an API key")
        for role in (update.reflection, update.maintenance):
            if (
                role.enabled
                and not role.inherit_main
                and role.provider is not None
                and not submitted[role.provider].api_key
                and not existing_keys[role.provider]
            ):
                raise ValueError(
                    f"model role provider '{role.provider.value}' requires an API key"
                )

        stored = StoredModelSettings(
            default_provider=update.default_provider,
            providers={
                item.provider.value: ProviderSettings.model_validate(
                    item.model_dump(exclude={"api_key"})
                )
                for item in update.providers
            },
            reflection=update.reflection,
            maintenance=update.maintenance,
        )
        for item in update.providers:
            if item.api_key:
                self.secrets.set(item.provider.value, item.api_key)
        self.store.save(stored)
        return stored

    async def test(self, item: ProviderSettingsUpdate) -> dict[str, Any]:
        _validate_connection_test_endpoint(item)
        key = item.api_key or self.secrets.get(item.provider.value)
        if not key:
            base_key = getattr(self.base_settings, _KEY_FIELDS[item.provider])
            key = base_key.get_secret_value() if base_key else None
        if not key:
            raise ValueError("API key is required before testing")
        config = ProviderConfig(
            provider=item.provider.value,
            model=item.model,
            api_key=SecretStr(key),
            api_style=item.api_style,
            base_url=item.base_url,
            timeout_seconds=30,
            max_retries=0,
            default_max_output_tokens=64,
        )
        adapter = (
            AnthropicAdapter(config)
            if item.provider is ModelProvider.ANTHROPIC
            else OpenAICompatibleAdapter(config)
        )
        started = time.perf_counter()
        try:
            response = await adapter.complete(
                ModelRequest(
                    messages=(
                        Message(role=MessageRole.USER, content="Reply with OK."),
                    ),
                    model=item.model,
                    max_output_tokens=64,
                    temperature=0,
                )
            )
        finally:
            await adapter.close()
        return {
            "success": True,
            "provider": response.provider,
            "model": response.model,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        }


def load_effective_model_configuration(
    *,
    store: ModelSettingsStore | None = None,
    secrets: ModelSecretStore | None = None,
    base_settings: ModelSettings | None = None,
) -> EffectiveModelConfiguration:
    """启动时合并.env、非敏感JSON和Keychain；显式设置优先。"""

    resolved_store = store or ModelSettingsStore()
    stored = resolved_store.load()
    base = base_settings or ModelSettings()
    if stored is None:
        return EffectiveModelConfiguration(base, None, None)
    resolved_secrets = secrets or MacOSKeychainSecretStore()
    data = base.model_dump(mode="python")
    data["model_default_provider"] = stored.default_provider
    for provider in ModelProvider:
        item = stored.providers[provider.value]
        prefix = provider.value
        data[f"{prefix}_model"] = item.model
        data[f"{prefix}_base_url"] = item.base_url
        if provider is not ModelProvider.ANTHROPIC:
            data[f"{prefix}_api_style"] = item.api_style
        secret = resolved_secrets.get(provider.value)
        if secret:
            data[_KEY_FIELDS[provider]] = SecretStr(secret)
    settings = ModelSettings.model_validate(data)
    reflection = _reflection_config(stored.reflection)
    maintenance = _maintenance_config(stored.maintenance)
    return EffectiveModelConfiguration(settings, reflection, maintenance)


def _defaults(settings: ModelSettings) -> StoredModelSettings:
    providers = {}
    for provider in ModelProvider:
        prefix = provider.value
        model = getattr(settings, f"{prefix}_model")
        base_url = getattr(settings, f"{prefix}_base_url")
        api_style = (
            getattr(settings, f"{prefix}_api_style")
            if provider is not ModelProvider.ANTHROPIC
            else "anthropic_messages"
        )
        providers[provider.value] = ProviderSettings(
            provider=provider,
            model=model,
            base_url=base_url,
            api_style=api_style,
        )
    return StoredModelSettings(
        default_provider=settings.model_default_provider,
        providers=providers,
    )


def _reflection_config(role: ModelRoleSettings) -> MemoryReflectionConfig:
    return MemoryReflectionConfig(
        enabled=role.enabled,
        provider=None if role.inherit_main else role.provider.value,
        model=None if role.inherit_main else role.model,
    )


def _maintenance_config(role: ModelRoleSettings) -> MemoryMaintenanceConfig:
    return MemoryMaintenanceConfig(
        enabled=role.enabled,
        provider=None if role.inherit_main else role.provider.value,
        model=None if role.inherit_main else role.model,
    )


def _secret_value(secret: SecretStr | None) -> str | None:
    if secret is None:
        return None
    return secret.get_secret_value().strip() or None


def _validate_connection_test_endpoint(item: ProviderSettingsUpdate) -> None:
    """仅允许设置页把密钥发送到内置 Provider 的官方 HTTPS 端点。"""

    expected_host = _OFFICIAL_TEST_HOSTS[item.provider]
    if item.base_url is None:
        if item.provider in {ModelProvider.OPENAI, ModelProvider.ANTHROPIC}:
            return
        raise ValueError("connection test requires the official provider endpoint")
    parsed = urlparse(item.base_url)
    if parsed.scheme != "https" or parsed.hostname != expected_host:
        raise ValueError(
            "connection test only supports the provider's official HTTPS endpoint"
        )


__all__ = [
    "EffectiveModelConfiguration",
    "ModelSettingsService",
    "load_effective_model_configuration",
]
