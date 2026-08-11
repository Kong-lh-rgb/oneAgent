"""Agent 执行过程的统一事件模型。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.types import Message, ModelUsage, ToolCall, ToolResult
from app.tools.approval import ApprovalDecision

from .result import AgentError, AgentResult, AgentStopReason


class AgentEventType(StrEnum):
    """Agent 生命周期中可产生的事件类型。"""

    AGENT_STARTED = "agent_started"
    MODEL_STARTED = "model_started"
    MODEL_COMPLETED = "model_completed"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    TOOL_APPROVAL_REQUIRED = "tool_approval_required"
    TOOL_APPROVAL_COMPLETED = "tool_approval_completed"
    MEMORY_REFLECTION_STARTED = "memory_reflection_started"
    MEMORY_REFLECTION_COMPLETED = "memory_reflection_completed"
    MEMORY_REFLECTION_FAILED = "memory_reflection_failed"
    MEMORY_REFLECTION_SKIPPED = "memory_reflection_skipped"
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
    result: AgentResult | None = None
    approval_decision: ApprovalDecision | None = None
    rule_id: str | None = None
    rule_description: str | None = None
    original_estimated_input_tokens: int | None = Field(default=None, ge=0)
    prepared_input_tokens: int | None = Field(default=None, ge=0)
    estimated_input_tokens: int | None = Field(default=None, ge=0)
    context_trimmed: bool | None = None
    context_window: int | None = Field(default=None, ge=0)
    input_budget: int | None = Field(default=None, ge=0)
    usage_ratio: float | None = Field(default=None, ge=0.0)
    trigger_tokens: int | None = Field(default=None, ge=0)
    target_tokens: int | None = Field(default=None, ge=0)
    requires_compaction: bool | None = None
    exceeds_input_budget: bool | None = None
    capability_source: str | None = None
    original_usage_ratio: float | None = Field(default=None, ge=0.0)
    prepared_usage_ratio: float | None = Field(default=None, ge=0.0)
    compaction_stage: str | None = None
    compacted_tool_results: int | None = Field(default=None, ge=0)
    removed_tool_rounds: int | None = Field(default=None, ge=0)
    reached_target: bool | None = None
    needs_next_compaction_stage: bool | None = None
    summary_updated: bool | None = None
    summarized_conversation_blocks: int | None = Field(default=None, ge=0)
    summary_usage: ModelUsage | None = None
    summary_error: str | None = None
    reflection_triggered: bool | None = None
    reflection_action: str | None = None
    reflection_duration_ms: float | None = Field(default=None, ge=0.0)
    reflection_error: str | None = None
    reflection_skip_reason: str | None = None
    reflection_memory_id: str | None = None
    reflection_maintenance_required: bool | None = None
    reflection_retention_candidate_ids: tuple[str, ...] = ()

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


class AgentEventHandler(ABC):
    """接收 Agent 运行事件的异步处理器。"""

    @abstractmethod
    async def emit(self, event: AgentEvent) -> None:
        """接收一个已经完成编号的事件。"""


class NullEventHandler(AgentEventHandler):
    """忽略全部事件的默认处理器。"""

    async def emit(self, event: AgentEvent) -> None:
        pass


class InMemoryEventHandler(AgentEventHandler):
    """在内存中按发射顺序保存事件，主要用于测试和调试。"""

    def __init__(self) -> None:
        self._events: list[AgentEvent] = []

    async def emit(self, event: AgentEvent) -> None:
        self._events.append(event)

    @property
    def events(self) -> tuple[AgentEvent, ...]:
        return tuple(self._events)

    def clear(self) -> None:
        self._events.clear()


class CompositeEventHandler(AgentEventHandler):
    """把同一个事件依次发送给多个相互隔离的处理器。"""

    def __init__(self, *handlers: AgentEventHandler) -> None:
        self._handlers = handlers

    async def emit(self, event: AgentEvent) -> None:
        for handler in self._handlers:
            try:
                await handler.emit(event)
            except Exception:
                # 单个观察者故障不影响其他事件消费者。
                continue


__all__ = [
    "AgentEvent",
    "AgentEventHandler",
    "AgentEventType",
    "CompositeEventHandler",
    "InMemoryEventHandler",
    "NullEventHandler",
]
