"""Automation 领域模型。

Automation 回答一个问题：
“未来什么时候，以什么 prompt，再启动一次 Agent Run？”

它只保存调度所需的最小信息（prompt / schedule / 执行元数据），
不保存 Run Trace、Tool Result、Checkpoint。

职责边界：
- Automation  —— 未来何时、以什么 prompt 启动 Run；
- RunManager  —— 这次 Run 的生命周期怎么管理；
- AgentRuntime —— Run 内部怎么执行。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator

_DEFAULT_TIMEZONE = "UTC"


class AutomationStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ScheduleKind(StrEnum):
    ONCE = "once"
    INTERVAL = "interval"
    CRON = "cron"


class Schedule(BaseModel):
    """自动化触发计划（V1 只支持三种）。

    - ONCE：``run_at``（必须带时区偏移的 ISO8601），执行一次；
    - INTERVAL：``interval_seconds``（>0），固定间隔重复；
    - CRON：``cron_expr``（crontab 五段表达式），简单 calendar recurrence。

    ``timezone`` 是 Schedule 的原时区语义（IANA 名）。内部持久化的
    ``next_run_at`` 统一转 UTC，但计算下一次触发始终基于 ``timezone``，
    避免"用 UTC 解释用户本地时间后又偷偷转换错"。
    """

    model_config = ConfigDict(extra="forbid")

    kind: ScheduleKind
    run_at: datetime | None = None
    interval_seconds: float | None = None
    cron_expr: str | None = None
    timezone: str = _DEFAULT_TIMEZONE

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"invalid timezone: {value}") from exc
        return value

    @field_validator("run_at")
    @classmethod
    def validate_run_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "run_at must include an explicit timezone offset (e.g. "
                "2026-08-20T09:00:00+08:00)"
            )
        return value.astimezone(UTC)

    @field_validator("interval_seconds")
    @classmethod
    def validate_interval(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if value <= 0:
            raise ValueError("interval_seconds must be > 0")
        return value


class Automation(BaseModel):
    """一条持久化的自动化记录（不保存 Trace / Tool Result / Checkpoint）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    title: str = ""
    prompt: str
    conversation_id: str | None = None
    status: AutomationStatus
    schedule: Schedule
    # 统一为 UTC；展示/计算下一次时基于 schedule.timezone。
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_run_id: str | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("id", "conversation_id", "last_run_id")
    @classmethod
    def normalize_identifier(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("automation identifiers cannot be empty")
        return normalized

    @field_validator(
        "next_run_at",
        "last_run_at",
        "created_at",
        "updated_at",
    )
    @classmethod
    def normalize_datetime(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("automation datetimes must include timezone information")
        return value.astimezone(UTC)


__all__ = [
    "Automation",
    "AutomationStatus",
    "Schedule",
    "ScheduleKind",
]
