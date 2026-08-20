"""Artifact 领域模型：Agent 显式发布的用户交付物（immutable result）。

不是 Trace / Checkpoint / 普通 ToolResult / Computer Screenshot —— 只有
Agent 通过 ``artifact_publish`` 显式发布的才算 Artifact。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ArtifactKind(StrEnum):
    FILE = "file"
    URL = "url"


class Artifact(BaseModel):
    """一个不可变的 Artifact 元数据（公开字段，不含内部 storage_path）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(default_factory=lambda: uuid4().hex)
    kind: ArtifactKind
    title: str = ""
    description: str | None = None
    filename: str | None = None
    mime_type: str | None = None
    size_bytes: int = 0
    sha256: str | None = None
    run_id: str | None = None
    conversation_id: str | None = None
    task_id: str | None = None
    source_url: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("id")
    @classmethod
    def id_required(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("artifact id cannot be empty")
        return normalized

    @field_validator("created_at")
    @classmethod
    def created_at_valid(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include timezone information")
        return value.astimezone(UTC)

    def public_dict(self) -> dict[str, object]:
        """转成可返回给 RPC / Desktop 的公开 dict（绝不包含 storage_path）。"""

        return {
            "id": self.id,
            "kind": self.kind.value,
            "title": self.title,
            "description": self.description,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "run_id": self.run_id,
            "conversation_id": self.conversation_id,
            "task_id": self.task_id,
            "source_url": self.source_url,
            "created_at": (
                self.created_at.isoformat() if self.created_at is not None else None
            ),
        }


__all__ = ["Artifact", "ArtifactKind"]
