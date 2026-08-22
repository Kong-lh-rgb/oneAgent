"""设置中心 V2 的模型配置数据结构。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.types import ApiStyle, ModelProvider


class ProviderSettings(BaseModel):
    """可持久化的单个 Provider 非敏感配置。"""

    model_config = ConfigDict(extra="forbid")

    provider: ModelProvider
    model: str
    base_url: str | None = None
    api_style: ApiStyle

    @field_validator("model", mode="before")
    @classmethod
    def normalize_model(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("model cannot be empty")
        return value.strip()

    @field_validator("base_url", mode="before")
    @classmethod
    def normalize_base_url(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("base_url must be a string")
        normalized = value.strip().rstrip("/")
        if not normalized:
            return None
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("base_url must use http or https")
        return normalized

    @model_validator(mode="after")
    def validate_provider_style(self) -> ProviderSettings:
        if (
            self.provider is ModelProvider.ANTHROPIC
            and self.api_style is not ApiStyle.ANTHROPIC_MESSAGES
        ):
            raise ValueError("anthropic requires anthropic_messages api style")
        if (
            self.provider is not ModelProvider.ANTHROPIC
            and self.api_style is ApiStyle.ANTHROPIC_MESSAGES
        ):
            raise ValueError("anthropic_messages is only valid for anthropic")
        return self


class ProviderSettingsUpdate(ProviderSettings):
    """保存/测试时可携带一次性明文密钥；该字段绝不进入JSON配置。"""

    api_key: str | None = Field(default=None, max_length=20_000)

    @field_validator("api_key", mode="before")
    @classmethod
    def normalize_api_key(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("api_key must be a string")
        return value.strip() or None


class ModelRoleSettings(BaseModel):
    """后台模型角色；inherit_main=True 时跟随主模型。"""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    inherit_main: bool = True
    provider: ModelProvider | None = None
    model: str | None = None

    @field_validator("model", mode="before")
    @classmethod
    def normalize_model(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("role model must be a string")
        return value.strip() or None

    @model_validator(mode="after")
    def validate_override(self) -> ModelRoleSettings:
        if self.inherit_main:
            return self
        if self.provider is None or self.model is None:
            raise ValueError("custom model role requires provider and model")
        return self


class StoredModelSettings(BaseModel):
    """落盘文件；明确不包含任何API Key。"""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    default_provider: ModelProvider
    providers: dict[str, ProviderSettings]
    reflection: ModelRoleSettings = Field(default_factory=ModelRoleSettings)
    maintenance: ModelRoleSettings = Field(default_factory=ModelRoleSettings)


class ModelSettingsUpdate(BaseModel):
    """Desktop提交的完整设置快照。"""

    model_config = ConfigDict(extra="forbid")

    default_provider: ModelProvider
    providers: tuple[ProviderSettingsUpdate, ...]
    reflection: ModelRoleSettings = Field(default_factory=ModelRoleSettings)
    maintenance: ModelRoleSettings = Field(default_factory=ModelRoleSettings)

    @model_validator(mode="after")
    def unique_providers(self) -> ModelSettingsUpdate:
        names = [item.provider for item in self.providers]
        if len(names) != len(set(names)):
            raise ValueError("provider settings contain duplicate providers")
        if set(names) != set(ModelProvider):
            raise ValueError("provider settings must contain every built-in provider")
        return self


__all__ = [
    "ModelRoleSettings",
    "ModelSettingsUpdate",
    "ProviderSettings",
    "ProviderSettingsUpdate",
    "StoredModelSettings",
]
