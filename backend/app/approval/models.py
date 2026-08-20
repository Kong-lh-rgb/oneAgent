"""Async Approval V1 数据模型。

ApprovalRequest 是“一次待人工决定 / 已决定的工具审批”的可持久化记录，
与 ``app/tools/approval.ApprovalRequest``（提交给审批门的请求上下文）区分开：

- 本模块 ApprovalRequest —— 持久化记录（id / status / created_at / resolved_at）；
- 工具层 ApprovalRequest  —— 审批门输入（tool_call_id / tool_name / arguments）。

status 只允许 PENDING → APPROVED / DENIED / CANCELLED（终态不可再修改），由
``SQLiteApprovalStore.resolve`` 在事务内强制。CANCELLED 表示"无人等待的孤儿
审批"：Run 被 cancel，或 Host 重启后找不到对应活跃 Run。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ApprovalRequestStatus(StrEnum):
    """ApprovalRequest 的生命周期状态。"""

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    CANCELLED = "cancelled"


class ApprovalRequest(BaseModel):
    """一次持久化的工具审批请求。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    run_id: str | None = None
    conversation_id: str | None = None
    tool_name: str = Field(min_length=1)
    tool_call_id: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    status: ApprovalRequestStatus = ApprovalRequestStatus.PENDING
    created_at: datetime
    resolved_at: datetime | None = None

    @field_validator("run_id", "conversation_id")
    @classmethod
    def normalize_identifier(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("approval identifiers cannot be empty")
        return normalized

    @field_validator("created_at", "resolved_at")
    @classmethod
    def normalize_datetime(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("approval datetimes must include timezone information")
        return value.astimezone(UTC)


__all__ = ["ApprovalRequest", "ApprovalRequestStatus"]
