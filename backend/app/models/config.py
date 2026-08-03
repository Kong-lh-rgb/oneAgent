"""由环境变量驱动的模型提供商配置。"""

from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from .errors import ProviderNotConfiguredError
from .types import ApiStyle, ModelProvider

_BACKEND_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class ProviderConfig(BaseModel):
    """用于创建单个适配器的完整配置。"""

    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    api_key: SecretStr
    api_style: ApiStyle
    base_url: str | None = None
    timeout_seconds: float = Field(default=120.0, gt=0)
    max_retries: int = Field(default=2, ge=0)
    default_max_output_tokens: int = Field(default=4096, gt=0)

    def api_key_value(self) -> str:
        return self.api_key.get_secret_value()


class ModelSettings(BaseSettings):
    """所有内置模型提供商的配置。

    配置先从 ``backend/.env`` 加载，再读取进程环境变量。
    进程环境变量的优先级更高。
    """

    model_config = SettingsConfigDict(
        env_file=_BACKEND_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    model_default_provider: ModelProvider = ModelProvider.OPENAI
    model_timeout_seconds: float = Field(default=120.0, gt=0)
    model_max_retries: int = Field(default=2, ge=0)
    model_default_max_output_tokens: int = Field(default=4096, gt=0)

    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-5.4-mini"
    openai_base_url: str | None = None
    openai_api_style: ApiStyle = ApiStyle.RESPONSES

    qwen_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("QWEN_API_KEY", "DASHSCOPE_API_KEY"),
    )
    qwen_model: str = "qwen3.7-plus"
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_api_style: ApiStyle = ApiStyle.CHAT_COMPLETIONS

    deepseek_api_key: SecretStr | None = None
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_api_style: ApiStyle = ApiStyle.CHAT_COMPLETIONS

    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-sonnet-4-6"
    anthropic_base_url: str | None = None

    def provider_config(
        self,
        provider: ModelProvider | str,
    ) -> ProviderConfig:
        provider_name = ModelProvider(provider)

        if provider_name is ModelProvider.OPENAI:
            return self._build_config(
                provider=provider_name,
                api_key=self.openai_api_key,
                key_variable="OPENAI_API_KEY",
                model=self.openai_model,
                base_url=self.openai_base_url,
                api_style=self.openai_api_style,
            )
        if provider_name is ModelProvider.QWEN:
            return self._build_config(
                provider=provider_name,
                api_key=self.qwen_api_key,
                key_variable="DASHSCOPE_API_KEY",
                model=self.qwen_model,
                base_url=self.qwen_base_url,
                api_style=self.qwen_api_style,
            )
        if provider_name is ModelProvider.DEEPSEEK:
            return self._build_config(
                provider=provider_name,
                api_key=self.deepseek_api_key,
                key_variable="DEEPSEEK_API_KEY",
                model=self.deepseek_model,
                base_url=self.deepseek_base_url,
                api_style=self.deepseek_api_style,
            )
        return self._build_config(
            provider=provider_name,
            api_key=self.anthropic_api_key,
            key_variable="ANTHROPIC_API_KEY",
            model=self.anthropic_model,
            base_url=self.anthropic_base_url,
            api_style=ApiStyle.ANTHROPIC_MESSAGES,
        )

    def configured_providers(self) -> tuple[ModelProvider, ...]:
        configured: list[ModelProvider] = []
        for provider, key in (
            (ModelProvider.OPENAI, self.openai_api_key),
            (ModelProvider.QWEN, self.qwen_api_key),
            (ModelProvider.DEEPSEEK, self.deepseek_api_key),
            (ModelProvider.ANTHROPIC, self.anthropic_api_key),
        ):
            if key is not None and key.get_secret_value():
                configured.append(provider)
        return tuple(configured)

    def _build_config(
        self,
        *,
        provider: ModelProvider,
        api_key: SecretStr | None,
        key_variable: str,
        model: str,
        base_url: str | None,
        api_style: ApiStyle,
    ) -> ProviderConfig:
        if api_key is None or not api_key.get_secret_value():
            raise ProviderNotConfiguredError(provider.value, key_variable)
        return ProviderConfig(
            provider=provider.value,
            model=model,
            api_key=api_key,
            api_style=api_style,
            base_url=base_url or None,
            timeout_seconds=self.model_timeout_seconds,
            max_retries=self.model_max_retries,
            default_max_output_tokens=self.model_default_max_output_tokens,
        )
