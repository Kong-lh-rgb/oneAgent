"""上下文窗口与预算配置。

按模型族提供默认上下文窗口，并配置预算策略（安全余量、触发/目标比例）
与用户覆盖项。覆盖项（``context_window_override`` / ``max_output_tokens_override``）
作用于当前使用的模型（显式指定 ``context_override_model``，否则作用于当前
配置的默认模型），不会全局应用到所有模型。
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, model_validator
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
    context_keep_recent_tool_rounds: int = Field(default=2, ge=0)
    context_keep_recent_conversation_blocks: int = Field(default=4, ge=0)
    context_summary_max_output_tokens: int = Field(default=1_024, gt=0)
    context_max_tool_result_chars: int = Field(default=8_000, gt=0)
    context_tool_result_head_chars: int = Field(default=4_000, ge=0)
    context_tool_result_tail_chars: int = Field(default=2_000, ge=0)

    # 覆盖配置：作用于当前使用的模型（context_override_model 未指定时用默认模型）
    context_override_provider: str | None = None
    context_override_model: str | None = None
    context_window_override: int | None = Field(default=None, gt=0)
    max_output_tokens_override: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_tool_result_segments(self) -> ContextSettings:
        """工具结果首尾保留长度不能超过压缩后的内容预算。"""

        retained = (
            self.context_tool_result_head_chars + self.context_tool_result_tail_chars
        )
        if retained > self.context_max_tool_result_chars:
            raise ValueError(
                "context tool result head/tail chars cannot exceed "
                "context_max_tool_result_chars"
            )
        return self


__all__ = ["ContextSettings"]
