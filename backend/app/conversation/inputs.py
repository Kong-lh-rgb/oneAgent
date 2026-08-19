"""Conversation 输入投递的轻量模型与 Trigger provenance。

Message schema 是 ``extra="forbid"`` 且没有 metadata 字段，因此不把来源元数据
硬编码进 user content，而是用独立的 ``TriggerContext`` 结构化传递来源：

- ``source=manual``：真人手动输入；
- ``source=automation``：由 Automation 定时投递（记录 automation_id /
  scheduled_for / triggered_at）。

上层（CLI / 未来 Desktop / Trace 展示）可通过 DispatchResult.trigger 区分
"Triggered by Automation xxx" 与真人输入。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ConversationSource(StrEnum):
    MANUAL = "manual"
    AUTOMATION = "automation"


class TriggerContext(BaseModel):
    """一次输入投递的触发来源与调度元数据。"""

    model_config = ConfigDict(extra="forbid")

    source: ConversationSource
    automation_id: str | None = None
    scheduled_for: datetime | None = None
    triggered_at: datetime | None = None

    @field_validator("scheduled_for", "triggered_at")
    @classmethod
    def normalize_datetime(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("trigger datetimes must include timezone information")
        return value.astimezone(UTC)


class ConversationInput(BaseModel):
    """一次要投递给某个 Conversation 的新输入消息。"""

    model_config = ConfigDict(extra="forbid")

    conversation_id: str | None = None
    content: str
    trigger: TriggerContext = Field(
        default_factory=lambda: TriggerContext(source=ConversationSource.MANUAL)
    )


__all__ = [
    "ConversationInput",
    "ConversationSource",
    "TriggerContext",
]
