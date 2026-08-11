"""Memory Maintenance 小模型的配置、输入与严格决策模型。"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.models.types import ModelUsage

_BACKEND_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
_MEMORY_ID_RE = re.compile(r"^M[0-9]{3,}$")


class MaintenanceAction(StrEnum):
    """容量维护 V1 允许执行的单动作。"""

    ARCHIVE = "archive"
    DEFER = "defer"


class MemoryMaintenanceConfig(BaseSettings):
    """独立容量维护模型与单次 Run 行为配置。"""

    model_config = SettingsConfigDict(
        env_file=_BACKEND_ENV_FILE,
        env_file_encoding="utf-8",
        env_prefix="MEMORY_MAINTENANCE_",
        extra="ignore",
    )

    enabled: bool = True
    provider: str | None = None
    model: str | None = None
    max_output_tokens: int = Field(default=800, gt=0)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    timeout_seconds: float = Field(default=30.0, gt=0.0)
    candidate_limit: int = Field(default=5, ge=1, le=10)
    max_actions: int = Field(default=3, ge=1, le=10)

    @field_validator("provider", "model", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("maintenance provider and model must be strings")
        return value.strip() or None


class MemoryMaintenanceCandidate(BaseModel):
    """交给维护模型判断的一条完整、只读候选快照。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    title: str
    summary: str
    content: str
    created_at: datetime
    updated_at: datetime
    last_accessed_at: datetime
    access_count: int = Field(ge=0)

    @field_validator("id", mode="before")
    @classmethod
    def normalize_id(cls, value: object) -> str:
        if not isinstance(value, str):
            raise TypeError("maintenance candidate ID must be a string")
        normalized = value.strip().upper()
        if not _MEMORY_ID_RE.fullmatch(normalized):
            raise ValueError("maintenance candidate ID must use the Mxxx format")
        return normalized


class MemoryMaintenanceInput(BaseModel):
    """一次容量决策所需的有界候选集合。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    active_count: int = Field(ge=0)
    max_active: int = Field(gt=0)
    required_slots: int = Field(default=0, ge=0)
    candidates: tuple[MemoryMaintenanceCandidate, ...]


class MemoryMaintenanceDecision(BaseModel):
    """维护模型的严格 archive/defer 单动作输出。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: MaintenanceAction
    memory_id: str | None = None
    reason: str

    @field_validator("memory_id", mode="before")
    @classmethod
    def normalize_optional_id(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("maintenance memory_id must be a string")
        normalized = value.strip().upper()
        if not _MEMORY_ID_RE.fullmatch(normalized):
            raise ValueError("maintenance memory_id must use the Mxxx format")
        return normalized

    @field_validator("reason", mode="before")
    @classmethod
    def normalize_reason(cls, value: object) -> str:
        if not isinstance(value, str):
            raise TypeError("maintenance reason must be a string")
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("maintenance reason cannot be empty")
        return normalized

    @model_validator(mode="after")
    def validate_action_fields(self) -> MemoryMaintenanceDecision:
        if self.action is MaintenanceAction.ARCHIVE and self.memory_id is None:
            raise ValueError("archive decision requires memory_id")
        if self.action is MaintenanceAction.DEFER and self.memory_id is not None:
            raise ValueError("defer decision cannot contain memory_id")
        return self


class MemoryMaintenanceProposal(BaseModel):
    """维护模型调用结果；文件 mutation 仍由 Harness 执行。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: MemoryMaintenanceDecision | None = None
    provider: str | None = None
    model: str | None = None
    duration_ms: float = Field(default=0.0, ge=0.0)
    usage: ModelUsage = Field(default_factory=ModelUsage)
    error: str | None = None


__all__ = [
    "MaintenanceAction",
    "MemoryMaintenanceCandidate",
    "MemoryMaintenanceConfig",
    "MemoryMaintenanceDecision",
    "MemoryMaintenanceInput",
    "MemoryMaintenanceProposal",
]
