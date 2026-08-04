"""Agent 执行过程的统一事件模型。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.types import Message, ModelUsage, ToolCall, ToolResult

from .result import AgentError, AgentStopReason


class AgentEventType(StrEnum):
    """Agent 生命周期中可产生的事件类型。"""

    AGENT_STARTED = "agent_started"
    MODEL_STARTED = "model_started"
    MODEL_COMPLETED = "model_completed"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    TOOL_APPROVAL_REQUIRED = "tool_approval_required"
    AGENT_COMPLETED = "agent_completed"
    AGENT_FAILED = "agent_failed"


class AgentEvent(BaseModel):
    """一次 Agent 运行中的可序列化事件。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str
    conversation_id: str | None = None
    sequence: int = Field(default=0, ge=0)
    type: AgentEventType
    event_time: datetime = Field(default_factory=lambda: datetime.now(UTC))
    step: int | None = Field(default=None, ge=1)
    provider: str | None = None
    model: str | None = None
    message: Message | None = None
    tool_call: ToolCall | None = None
    tool_result: ToolResult | None = None
    usage: ModelUsage | None = None
    stop_reason: AgentStopReason | None = None
    error: AgentError | None = None

    @field_validator("run_id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        """运行 ID 必须是非空字符串。"""

        normalized = value.strip()
        if not normalized:
            raise ValueError("run_id cannot be empty")
        return normalized

    @field_validator("event_time")
    @classmethod
    def normalize_event_time(cls, value: datetime) -> datetime:
        """要求事件时间包含时区，并统一转换为 UTC。"""

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("event_time must include timezone information")
        return value.astimezone(UTC)


__all__ = ["AgentEvent", "AgentEventType"]
