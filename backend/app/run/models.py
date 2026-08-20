"""Run 生命周期模型。

Run 是 oneAgent 中“一次 Agent 执行”的生命周期索引，不是 Trace、不是
Checkpoint、也不是 Conversation：

- Run      —— “这次执行现在是什么生命周期状态？”（可持久化、可查询、可取消、可恢复）
- Checkpoint —— “这次执行中断以后从哪里恢复？”（最小可恢复状态，见 app/checkpoint）
- Trace    —— “这次执行到底发生过什么？”（事件记录，见 app/trace）
- Task     —— “业务任务当前推进到哪里？”（见 app/task）

一个 Conversation 可以产生多个 Run；一个 Task 可以关联多个 Run。Run 不复制
Conversation / Events / Tool Results，也不复制 Checkpoint 数据结构。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.types import AgentMode


class RunStatus(StrEnum):
    """Run 的生命周期状态。

    COMPLETED / FAILED / CANCELLED / INTERRUPTED 都是终态（不可再转换）。

    一个 Run = 一次 execution attempt：INTERRUPTED 表示这次 attempt 被中断、
    到此结束；恢复不是把旧 Run 重新置回 RUNNING，而是由 recover() 创建一次
    新的 attempt（新 Run 记录 recovered_from_run_id 指向旧 Run）。

    与 app/trace RunStatus（RUNNING/COMPLETED/FAILED）保持值语义一致，
    这里扩展了 PENDING / CANCELLED / INTERRUPTED 三个生命周期状态。
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


# 允许的状态转换。
# - PENDING → RUNNING：start() 创建后立即开始执行；
# - PENDING → FAILED：仅进程重启 reconciliation 使用 —— 进程崩溃前创建但
#   从未开始执行的 PENDING Run 不可能再启动，也没有 Checkpoint 可恢复，
#   统一归入 FAILED 终态（不留 PENDING 永久卡住）。
# - RUNNING → 终态 / INTERRUPTED：执行结束、失败、取消、进程中断。
# INTERRUPTED 也是终态：中断后该 attempt 结束；恢复 = recover() 创建新的
# execution attempt（旧 Run 永远保持 INTERRUPTED），不会重新进入 RUNNING。
_ALLOWED_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.PENDING: frozenset({RunStatus.RUNNING, RunStatus.FAILED}),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.INTERRUPTED,
        }
    ),
    RunStatus.INTERRUPTED: frozenset(),
    RunStatus.COMPLETED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}

TERMINAL_STATUSES = frozenset(
    {
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
        RunStatus.INTERRUPTED,
    }
)


class Run(BaseModel):
    """一次 Agent Run 的生命周期记录（轻量索引，不保存事件/工具结果）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    conversation_id: str | None = None
    status: RunStatus
    user_message: str = ""
    created_at: datetime
    started_at: datetime | None = None
    updated_at: datetime
    completed_at: datetime | None = None
    error: str | None = None
    stop_reason: str | None = None
    # recover 语义：recover() 启动的新 Run 在创建时一次性记录它恢复自哪个旧 Run；
    # 旧 Run 保持 INTERRUPTED（生命周期事实不伪造为 COMPLETED）。
    recovered_from_run_id: str | None = None
    # 触发来源（轻量 provenance，随 Run 持久化）：
    #   source       —— manual | automation
    #   source_id    —— automation_id（或其它来源标识）
    #   scheduled_for / triggered_at —— Automation 调度语义
    source: str | None = None
    source_id: str | None = None
    scheduled_for: datetime | None = None
    triggered_at: datetime | None = None
    # 一次执行的模式（NORMAL / PLAN）。不是生命周期状态，只是输入语义。
    mode: AgentMode = AgentMode.NORMAL

    @field_validator("id", "conversation_id", "recovered_from_run_id", "source_id")
    @classmethod
    def normalize_identifier(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("run identifiers cannot be empty")
        return normalized

    @field_validator(
        "created_at",
        "started_at",
        "updated_at",
        "completed_at",
        "scheduled_for",
        "triggered_at",
    )
    @classmethod
    def normalize_datetime(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("run datetimes must include timezone information")
        return value.astimezone(UTC)


__all__ = [
    "TERMINAL_STATUSES",
    "Run",
    "RunStatus",
    "_ALLOWED_TRANSITIONS",
]
