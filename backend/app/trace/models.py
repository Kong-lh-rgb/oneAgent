"""Agent Trace 使用的数据结构。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.agent.result import AgentStopReason
from app.models.types import ModelUsage


class RunStatus(StrEnum):
    """一次 Agent 运行的持久化状态。"""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AgentRunTrace(BaseModel):
    """用于列表和查询的一次 Agent 运行摘要。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    conversation_id: str | None = None
    status: RunStatus
    started_at: datetime
    completed_at: datetime | None = None
    provider: str | None = None
    model: str | None = None
    steps: int = Field(default=0, ge=0)
    stop_reason: AgentStopReason | None = None
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    event_count: int = Field(default=0, ge=0)


class RunUsageSummary(BaseModel):
    """一次 Run 的完整 Provider Usage 账本。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    main_agent: ModelUsage = Field(default_factory=ModelUsage)
    memory_reflection: ModelUsage = Field(default_factory=ModelUsage)
    memory_maintenance: ModelUsage = Field(default_factory=ModelUsage)
    provider_total: ModelUsage = Field(default_factory=ModelUsage)
    tool_schema_tokens_estimated: int = Field(default=0, ge=0)
    memory_reflection_status: str = "not_run"
    memory_reflection_skip_reason: str | None = None
