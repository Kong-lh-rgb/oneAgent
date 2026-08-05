"""上下文窗口与预算配置。

按模型族提供默认上下文窗口，并配置预算策略（安全余量、触发/目标比例）
与用户覆盖项。覆盖项（``context_window_override`` / ``max_output_tokens_override``）
作用于当前使用的模型（显式指定 ``context_override_model``，否则作用于当前
配置的默认模型），不会全局应用到所有模型。
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class ContextSettings(BaseSettings):
    """上下文窗口与预算配置。"""

    model_config = SettingsConfigDict(
        env_file=_BACKEND_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Provider 默认上下文窗口（用于 Provider 默认能力）
    context_window_default: int = Field(default=128_000, gt=0)
    context_window_openai: int = Field(default=200_000, gt=0)
    context_window_qwen: int = Field(default=1_000_000, gt=0)
    context_window_deepseek: int = Field(default=1_048_576, gt=0)
    context_window_anthropic: int = Field(default=200_000, gt=0)

    # 预算策略
    context_safety_margin_tokens: int = Field(default=4_096, ge=0)
    context_trigger_ratio: float = Field(default=0.80, gt=0.0, lt=1.0)
    context_target_ratio: float = Field(default=0.60, gt=0.0, lt=1.0)

    # 覆盖配置：作用于当前使用的模型（context_override_model 未指定时用默认模型）
    context_override_provider: str | None = None
    context_override_model: str | None = None
    context_window_override: int | None = Field(default=None, gt=0)
    max_output_tokens_override: int | None = Field(default=None, gt=0)


__all__ = ["ContextSettings"]
