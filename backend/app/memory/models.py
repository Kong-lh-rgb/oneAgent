"""长期记忆的领域模型。"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_MAX_CONTENT_CHARS = 4_000


class MemoryType(StrEnum):
    """长期记忆的三种基本语义。"""

    FACT = "fact"
    EPISODE = "episode"
    PROCEDURE = "procedure"


class MemoryStatus(StrEnum):
    """记忆从候选到退出检索的生命周期。"""

    CANDIDATE = "candidate"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


class MemorySource(BaseModel):
    """指回原始事实的来源锚点。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str | None = None
    run_id: str | None = None
    message_id: str | None = None

    @field_validator("session_id", "run_id", "message_id", mode="before")
    @classmethod
    def normalize_identifier(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("memory source identifiers must be strings")
        normalized = value.strip()
        if not normalized:
            return None
        if len(normalized) > 500:
            raise ValueError("memory source identifier is too long")
        return normalized

    @model_validator(mode="after")
    def require_parent_source(self) -> MemorySource:
        if self.message_id and not self.session_id:
            raise ValueError("source message_id requires session_id")
        return self


class MemoryDraft(BaseModel):
    """经过提取、尚未进入 Store 的记忆候选。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    namespace: str
    memory_type: MemoryType
    content: str
    key: str | None = None
    status: MemoryStatus = MemoryStatus.CANDIDATE
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: MemorySource = Field(default_factory=MemorySource)
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("namespace", "content", mode="before")
    @classmethod
    def normalize_required_text(cls, value: object) -> str:
        if not isinstance(value, str):
            raise TypeError("memory text fields must be strings")
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("memory text fields cannot be empty")
        if len(normalized) > _MAX_CONTENT_CHARS:
            raise ValueError("memory text field is too long")
        return normalized

    @field_validator("key", mode="before")
    @classmethod
    def normalize_key(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("memory key must be a string")
        normalized = value.strip().lower()
        return normalized or None

    @model_validator(mode="after")
    def validate_key(self) -> MemoryDraft:
        if self.memory_type is MemoryType.FACT and self.status is MemoryStatus.ACTIVE:
            if not self.key:
                raise ValueError("active FACT memory requires a key")
        if self.status not in {MemoryStatus.CANDIDATE, MemoryStatus.ACTIVE}:
            raise ValueError("new memory must be candidate or active")
        return self


class MemoryItem(BaseModel):
    """已经持久化、可追踪生命周期的一条记忆。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    namespace: str
    memory_type: MemoryType
    key: str | None = None
    content: str
    normalized_content: str
    fingerprint: str
    status: MemoryStatus
    importance: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    source: MemorySource
    access_count: int = Field(default=0, ge=0)
    use_count: int = Field(default=0, ge=0)
    confirmation_count: int = Field(default=0, ge=0)
    last_accessed_at: datetime | None = None
    supersedes_id: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    status_changed_at: datetime | None = None
    revision: int = Field(default=1, ge=1)

    @field_validator("id", "supersedes_id", mode="before")
    @classmethod
    def normalize_id(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("memory ids must be strings")
        normalized = value.strip().lower()
        if not _ID_RE.fullmatch(normalized):
            raise ValueError("memory id must be 32 lowercase hexadecimal characters")
        return normalized

    @field_validator(
        "created_at",
        "updated_at",
        "last_accessed_at",
        "status_changed_at",
    )
    @classmethod
    def normalize_datetime(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("memory datetimes must include timezone information")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_invariants(self) -> MemoryItem:
        if self.supersedes_id == self.id:
            raise ValueError("memory cannot supersede itself")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at")
        if self.status in {MemoryStatus.SUPERSEDED, MemoryStatus.ARCHIVED}:
            if self.status_changed_at is None:
                raise ValueError("inactive memory requires status_changed_at")
        return self


class MemorySearchResult(BaseModel):
    """混合检索返回的一条可解释结果。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    memory: MemoryItem
    score: float
    lexical_rank: int | None = None
    vector_rank: int | None = None
    lexical_score: float | None = None
    vector_distance: float | None = None


def utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "MemoryDraft",
    "MemoryItem",
    "MemorySearchResult",
    "MemorySource",
    "MemoryStatus",
    "MemoryType",
    "utc_now",
]
