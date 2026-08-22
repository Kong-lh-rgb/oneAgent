"""设置中心 V2 模型配置的离线测试。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.model_settings import (
    ModelSettingsService,
    ModelSettingsStore,
    ModelSettingsUpdate,
    ProviderSettingsUpdate,
    load_effective_model_configuration,
)
from app.models.config import ModelSettings
from app.models.types import ApiStyle, ModelProvider


@dataclass
class FakeSecrets:
    values: dict[str, str] = field(default_factory=dict)
    reads: list[str] = field(default_factory=list)

    def get(self, provider: str) -> str | None:
        self.reads.append(provider)
        return self.values.get(provider)

    def set(self, provider: str, value: str) -> None:
        self.values[provider] = value


def _base_settings() -> ModelSettings:
    return ModelSettings(
        _env_file=None,
        model_default_provider=ModelProvider.OPENAI,
        openai_api_key=None,
        qwen_api_key=None,
        deepseek_api_key=None,
        anthropic_api_key=None,
    )


def _update() -> ModelSettingsUpdate:
    return ModelSettingsUpdate(
        default_provider=ModelProvider.QWEN,
        providers=(
            ProviderSettingsUpdate(
                provider=ModelProvider.OPENAI,
                model="gpt-test",
                api_style=ApiStyle.RESPONSES,
            ),
            ProviderSettingsUpdate(
                provider=ModelProvider.QWEN,
                model="qwen-test",
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                api_style=ApiStyle.CHAT_COMPLETIONS,
                api_key="secret-qwen",
            ),
            ProviderSettingsUpdate(
                provider=ModelProvider.DEEPSEEK,
                model="deepseek-test",
                base_url="https://api.deepseek.com",
                api_style=ApiStyle.CHAT_COMPLETIONS,
            ),
            ProviderSettingsUpdate(
                provider=ModelProvider.ANTHROPIC,
                model="claude-test",
                api_style=ApiStyle.ANTHROPIC_MESSAGES,
            ),
        ),
        reflection={
            "enabled": True,
            "inherit_main": False,
            "provider": "qwen",
            "model": "qwen-small",
        },
        maintenance={
            "enabled": False,
            "inherit_main": False,
            "provider": "openai",
            "model": "gpt-small",
        },
        summary={
            "enabled": True,
            "inherit_main": False,
            "provider": "qwen",
            "model": "qwen-summary",
        },
    )


def test_save_keeps_api_key_out_of_json(tmp_path: Path) -> None:
    path = tmp_path / "models.json"
    secrets = FakeSecrets()
    service = ModelSettingsService(
        store=ModelSettingsStore(path),
        secrets=secrets,
        base_settings=_base_settings(),
    )

    service.save(_update())

    raw = path.read_text(encoding="utf-8")
    assert "secret-qwen" not in raw
    assert "api_key" not in raw
    assert secrets.values == {"qwen": "secret-qwen"}
    assert json.loads(raw)["default_provider"] == "qwen"


def test_effective_configuration_merges_roles_and_keychain(tmp_path: Path) -> None:
    store = ModelSettingsStore(tmp_path / "models.json")
    secrets = FakeSecrets()
    service = ModelSettingsService(
        store=store,
        secrets=secrets,
        base_settings=_base_settings(),
    )
    service.save(_update())

    effective = load_effective_model_configuration(
        store=store,
        secrets=secrets,
        base_settings=_base_settings(),
    )

    assert effective.settings.model_default_provider is ModelProvider.QWEN
    assert effective.settings.qwen_model == "qwen-test"
    assert effective.settings.qwen_api_key == SecretStr("secret-qwen")
    assert effective.reflection is not None
    assert effective.reflection.provider == "qwen"
    assert effective.reflection.model == "qwen-small"
    assert effective.maintenance is not None
    assert effective.maintenance.enabled is False
    assert effective.summary is not None
    assert effective.summary.enabled is True
    assert effective.summary.provider == "qwen"
    assert effective.summary.model == "qwen-summary"


def test_old_settings_without_summary_use_inherited_enabled_default(
    tmp_path: Path,
) -> None:
    path = tmp_path / "models.json"
    ModelSettingsService(
        store=ModelSettingsStore(path),
        secrets=FakeSecrets(),
        base_settings=_base_settings(),
    ).save(_update())
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("summary")
    path.write_text(json.dumps(payload), encoding="utf-8")

    stored = ModelSettingsStore(path).load()

    assert stored is not None
    assert stored.summary.enabled is True
    assert stored.summary.inherit_main is True


@pytest.mark.asyncio
async def test_connection_test_rejects_custom_endpoint_before_reading_key(
    tmp_path: Path,
) -> None:
    secrets = FakeSecrets(values={"openai": "must-not-be-read"})
    service = ModelSettingsService(
        store=ModelSettingsStore(tmp_path / "models.json"),
        secrets=secrets,
        base_settings=_base_settings(),
    )
    provider = ProviderSettingsUpdate(
        provider=ModelProvider.OPENAI,
        model="proxy-model",
        base_url="https://example.invalid/v1",
        api_style=ApiStyle.RESPONSES,
    )

    with pytest.raises(ValueError, match="official HTTPS endpoint"):
        await service.test(provider)

    assert secrets.reads == []


def test_default_provider_without_any_key_is_rejected(tmp_path: Path) -> None:
    update = _update().model_copy(
        update={
            "providers": tuple(
                item.model_copy(update={"api_key": None})
                for item in _update().providers
            )
        }
    )
    service = ModelSettingsService(
        store=ModelSettingsStore(tmp_path / "models.json"),
        secrets=FakeSecrets(),
        base_settings=_base_settings(),
    )

    with pytest.raises(ValueError, match="default provider requires an API key"):
        service.save(update)
