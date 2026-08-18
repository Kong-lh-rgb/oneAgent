"""Skill 运行配置。"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class SkillSettings(BaseSettings):
    """Skill 上下文预算等运行配置。"""

    model_config = SettingsConfigDict(
        env_file=_BACKEND_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Active Skill 指令的总上下文预算（token）。
    skill_context_max_tokens: int = Field(default=4_096, gt=0)
    # 同一 Run 最多同时激活的 Skill 数量。
    skill_max_active: int = Field(default=4, gt=0)
    # Skill Catalog（name + description）每 Step 注入的独立 Token 预算。
    skill_catalog_max_tokens: int = Field(default=2_048, gt=0)


__all__ = ["SkillSettings"]
