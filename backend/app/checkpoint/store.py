"""基于现有 Vesta SQLite 文件的 Run Checkpoint 存储。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from app.agent.result import AgentStopReason
from app.conversation import DEFAULT_DATABASE_PATH
from app.models.types import Message, MessageRole, ToolCall, ToolResult

from .models import CheckpointPhase, CheckpointStatus, RunCheckpoint

_SCHEMA = """
CREATE TABLE IF NOT EXISTS run_checkpoints (
    run_id TEXT PRIMARY KEY,
    conversation_id TEXT,
    user_message_json TEXT NOT NULL,
    status TEXT NOT NULL,
    phase TEXT NOT NULL,
    step INTEGER NOT NULL DEFAULT 0,
    pending_tool_calls_json TEXT NOT NULL DEFAULT '[]',
    completed_tool_results_json TEXT NOT NULL DEFAULT '[]',
    stop_reason TEXT,
    error TEXT,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    recovered_by_run_id TEXT,
    revision INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_run_checkpoints_conversation_updated
ON run_checkpoints(conversation_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_run_checkpoints_status
ON run_checkpoints(status, updated_at DESC);
"""


class SQLiteCheckpointStore:
    """持久化 Run 的最后确认边界，并提供中断恢复查询。"""

    def __init__(self, database_path: str | Path = DEFAULT_DATABASE_PATH) -> None:
        self.database_path = Path(database_path).expanduser().resolve()

    async def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        async with self._connect() as database:
            await database.executescript(_SCHEMA)
            await database.commit()

    async def start(
        self,
        run_id: str,
        *,
        conversation_id: str | None,
        user_message: Message,
    ) -> RunCheckpoint:
        """创建 running Checkpoint；重复 run_id 会被拒绝。"""

        if user_message.role is not MessageRole.USER:
            raise ValueError("checkpoint user_message must have user role")
        now = datetime.now(UTC).isoformat()
        async with self._connect() as database:
            try:
                await database.execute(
                    """
                    INSERT INTO run_checkpoints (
                        run_id, conversation_id, user_message_json, status, phase,
                        started_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _required_identifier(run_id, "run_id"),
                        _optional_identifier(conversation_id),
                        user_message.model_dump_json(),
                        CheckpointStatus.RUNNING.value,
                        CheckpointPhase.STARTING.value,
                        now,
                        now,
                    ),
                )
            except aiosqlite.IntegrityError as exc:
                raise ValueError(f"Checkpoint 已存在：{run_id}") from exc
            await database.commit()
        return await self._require(run_id)

    async def before_model(self, run_id: str, *, step: int) -> RunCheckpoint:
        """在发起模型请求前保存边界。"""

        return await self._update_running(
            run_id,
            phase=CheckpointPhase.MODEL_REQUEST,
            step=step,
            pending_tool_calls=(),
        )

    async def before_tools(
        self,
        run_id: str,
        *,
        step: int,
        tool_calls: Sequence[ToolCall],
    ) -> RunCheckpoint:
        """工具执行前先持久化全部待执行调用。"""

        if not tool_calls:
            raise ValueError("before_tools requires at least one tool call")
        return await self._update_running(
            run_id,
            phase=CheckpointPhase.TOOL_EXECUTION,
            step=step,
            pending_tool_calls=tuple(tool_calls),
        )

    async def complete_tool(
        self,
        run_id: str,
        result: ToolResult,
    ) -> RunCheckpoint:
        """记录一个工具的统一结果，并从待执行集合移除。"""

        async with self._connect() as database:
            await database.execute("BEGIN IMMEDIATE")
            checkpoint = await _require_row(database, run_id)
            _require_running(checkpoint)
            pending = list(checkpoint.pending_tool_calls)
            matching = [
                call for call in pending if call.id == result.tool_call_id
            ]
            if len(matching) != 1:
                raise ValueError(
                    f"工具调用不在 Checkpoint 待执行集合中：{result.tool_call_id}"
                )
            pending = [call for call in pending if call.id != result.tool_call_id]
            completed = (*checkpoint.completed_tool_results, result)
            phase = (
                CheckpointPhase.TOOL_EXECUTION
                if pending
                else CheckpointPhase.TOOL_RESULTS_READY
            )
            await _write_progress(
                database,
                checkpoint,
                phase=phase,
                step=checkpoint.step,
                pending_tool_calls=pending,
                completed_tool_results=completed,
            )
            await database.commit()
        return await self._require(run_id)

    async def complete(
        self,
        run_id: str,
        *,
        stop_reason: AgentStopReason,
    ) -> RunCheckpoint:
        return await self._finish(
            run_id,
            status=CheckpointStatus.COMPLETED,
            stop_reason=stop_reason,
        )

    async def fail(
        self,
        run_id: str,
        *,
        stop_reason: AgentStopReason,
        error: str | None,
    ) -> RunCheckpoint:
        return await self._finish(
            run_id,
            status=CheckpointStatus.FAILED,
            stop_reason=stop_reason,
            error=error,
        )

    async def interrupt(
        self,
        run_id: str,
        *,
        error: str | None = None,
    ) -> RunCheckpoint:
        """保留当前 phase 与未决工具，仅把 running 标记为 interrupted。"""

        return await self._finish(
            run_id,
            status=CheckpointStatus.INTERRUPTED,
            error=error,
            preserve_phase=True,
        )

    async def recover_running(
        self,
        *,
        conversation_id: str | None = None,
    ) -> tuple[RunCheckpoint, ...]:
        """启动时把遗留 running 转成 interrupted，并返回恢复记录。"""

        query = "SELECT run_id FROM run_checkpoints WHERE status = ?"
        parameters: list[object] = [CheckpointStatus.RUNNING.value]
        if conversation_id is not None:
            query += " AND conversation_id = ?"
            parameters.append(_required_identifier(conversation_id, "conversation_id"))
        async with self._connect() as database:
            cursor = await database.execute(query, tuple(parameters))
            run_ids = [row["run_id"] for row in await cursor.fetchall()]
        recovered: list[RunCheckpoint] = []
        for run_id in run_ids:
            recovered.append(
                await self.interrupt(
                    run_id,
                    error="process ended before Run reached a terminal state",
                )
            )
        return tuple(recovered)

    async def latest_unrecovered(
        self,
        conversation_id: str,
    ) -> RunCheckpoint | None:
        """返回当前会话最近一条尚未核对的中断记录。"""

        async with self._connect() as database:
            cursor = await database.execute(
                """
                SELECT * FROM run_checkpoints
                WHERE conversation_id = ?
                  AND status = ?
                  AND recovered_by_run_id IS NULL
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (
                    _required_identifier(conversation_id, "conversation_id"),
                    CheckpointStatus.INTERRUPTED.value,
                ),
            )
            row = await cursor.fetchone()
        return _checkpoint_from_row(row) if row is not None else None

    async def get_unrecovered(self, run_id: str) -> RunCheckpoint | None:
        """返回指定 Run 的未核对中断记录（仅 INTERRUPTED 且未 recovered）。

        供 RunManager.recover() 精确定位要恢复的 Checkpoint，而不是按会话
        取最近一条（避免会话内存在多个中断 Run 时恢复错对象）。
        """

        async with self._connect() as database:
            cursor = await database.execute(
                """
                SELECT * FROM run_checkpoints
                WHERE run_id = ?
                  AND status = ?
                  AND recovered_by_run_id IS NULL
                LIMIT 1
                """,
                (
                    _required_identifier(run_id, "run_id"),
                    CheckpointStatus.INTERRUPTED.value,
                ),
            )
            row = await cursor.fetchone()
        return _checkpoint_from_row(row) if row is not None else None

    async def mark_recovered(
        self,
        interrupted_run_id: str,
        *,
        recovered_by_run_id: str,
    ) -> RunCheckpoint:
        """在后续 Run 正常完成后标记中断记录已被模型处理。"""

        async with self._connect() as database:
            checkpoint = await _require_row(database, interrupted_run_id)
            if checkpoint.status is not CheckpointStatus.INTERRUPTED:
                raise ValueError("only interrupted checkpoint can be recovered")
            await database.execute(
                """
                UPDATE run_checkpoints
                SET recovered_by_run_id = ?, updated_at = ?, revision = revision + 1
                WHERE run_id = ?
                """,
                (
                    _required_identifier(recovered_by_run_id, "recovered_by_run_id"),
                    datetime.now(UTC).isoformat(),
                    interrupted_run_id,
                ),
            )
            await database.commit()
        return await self._require(interrupted_run_id)

    async def get(self, run_id: str) -> RunCheckpoint | None:
        async with self._connect() as database:
            cursor = await database.execute(
                "SELECT * FROM run_checkpoints WHERE run_id = ?",
                (_required_identifier(run_id, "run_id"),),
            )
            row = await cursor.fetchone()
        return _checkpoint_from_row(row) if row is not None else None

    async def list(
        self,
        *,
        conversation_id: str | None = None,
        limit: int = 20,
    ) -> tuple[RunCheckpoint, ...]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        query = "SELECT * FROM run_checkpoints"
        parameters: list[object] = []
        if conversation_id is not None:
            query += " WHERE conversation_id = ?"
            parameters.append(_required_identifier(conversation_id, "conversation_id"))
        query += " ORDER BY updated_at DESC LIMIT ?"
        parameters.append(limit)
        async with self._connect() as database:
            cursor = await database.execute(query, tuple(parameters))
            rows = await cursor.fetchall()
        return tuple(_checkpoint_from_row(row) for row in rows)

    async def _update_running(
        self,
        run_id: str,
        *,
        phase: CheckpointPhase,
        step: int,
        pending_tool_calls: Sequence[ToolCall],
    ) -> RunCheckpoint:
        if step < 1:
            raise ValueError("checkpoint step must be at least 1")
        async with self._connect() as database:
            await database.execute("BEGIN IMMEDIATE")
            checkpoint = await _require_row(database, run_id)
            _require_running(checkpoint)
            if checkpoint.pending_tool_calls:
                raise ValueError(
                    "cannot advance checkpoint while tool calls are pending"
                )
            await _write_progress(
                database,
                checkpoint,
                phase=phase,
                step=step,
                pending_tool_calls=pending_tool_calls,
                completed_tool_results=checkpoint.completed_tool_results,
            )
            await database.commit()
        return await self._require(run_id)

    async def _finish(
        self,
        run_id: str,
        *,
        status: CheckpointStatus,
        stop_reason: AgentStopReason | None = None,
        error: str | None = None,
        preserve_phase: bool = False,
    ) -> RunCheckpoint:
        async with self._connect() as database:
            await database.execute("BEGIN IMMEDIATE")
            checkpoint = await _require_row(database, run_id)
            _require_running(checkpoint)
            if (
                status is CheckpointStatus.COMPLETED
                and checkpoint.pending_tool_calls
            ):
                raise ValueError(
                    "completed checkpoint cannot contain pending tool calls"
                )
            now = datetime.now(UTC).isoformat()
            await database.execute(
                """
                UPDATE run_checkpoints
                SET status = ?, phase = ?, stop_reason = ?, error = ?,
                    updated_at = ?, completed_at = ?, revision = revision + 1
                WHERE run_id = ?
                """,
                (
                    status.value,
                    (
                        checkpoint.phase.value
                        if preserve_phase
                        else CheckpointPhase.FINISHED.value
                    ),
                    stop_reason.value if stop_reason else None,
                    error,
                    now,
                    now,
                    run_id,
                ),
            )
            await database.commit()
        return await self._require(run_id)

    async def _require(self, run_id: str) -> RunCheckpoint:
        checkpoint = await self.get(run_id)
        if checkpoint is None:
            raise KeyError(f"Checkpoint 不存在：{run_id}")
        return checkpoint

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[aiosqlite.Connection]:
        database = await aiosqlite.connect(self.database_path)
        database.row_factory = aiosqlite.Row
        try:
            yield database
        finally:
            await database.close()


async def _require_row(
    database: aiosqlite.Connection,
    run_id: str,
) -> RunCheckpoint:
    cursor = await database.execute(
        "SELECT * FROM run_checkpoints WHERE run_id = ?",
        (_required_identifier(run_id, "run_id"),),
    )
    row = await cursor.fetchone()
    if row is None:
        raise KeyError(f"Checkpoint 不存在：{run_id}")
    return _checkpoint_from_row(row)


async def _write_progress(
    database: aiosqlite.Connection,
    checkpoint: RunCheckpoint,
    *,
    phase: CheckpointPhase,
    step: int,
    pending_tool_calls: Sequence[ToolCall],
    completed_tool_results: Sequence[ToolResult],
) -> None:
    await database.execute(
        """
        UPDATE run_checkpoints
        SET phase = ?, step = ?, pending_tool_calls_json = ?,
            completed_tool_results_json = ?, updated_at = ?,
            revision = revision + 1
        WHERE run_id = ?
        """,
        (
            phase.value,
            step,
            _dump_models(pending_tool_calls),
            _dump_models(completed_tool_results),
            datetime.now(UTC).isoformat(),
            checkpoint.run_id,
        ),
    )


def _require_running(checkpoint: RunCheckpoint) -> None:
    if checkpoint.status is not CheckpointStatus.RUNNING:
        raise ValueError(
            f"Checkpoint 已结束，不能继续更新：{checkpoint.run_id}"
        )


def _checkpoint_from_row(row: aiosqlite.Row) -> RunCheckpoint:
    return RunCheckpoint(
        run_id=row["run_id"],
        conversation_id=row["conversation_id"],
        user_message=Message.model_validate_json(row["user_message_json"]),
        status=row["status"],
        phase=row["phase"],
        step=row["step"],
        pending_tool_calls=tuple(
            ToolCall.model_validate(item)
            for item in json.loads(row["pending_tool_calls_json"])
        ),
        completed_tool_results=tuple(
            ToolResult.model_validate(item)
            for item in json.loads(row["completed_tool_results_json"])
        ),
        stop_reason=row["stop_reason"],
        error=row["error"],
        started_at=datetime.fromisoformat(row["started_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        completed_at=(
            datetime.fromisoformat(row["completed_at"])
            if row["completed_at"]
            else None
        ),
        recovered_by_run_id=row["recovered_by_run_id"],
        revision=row["revision"],
    )


def _dump_models(models: Sequence[ToolCall] | Sequence[ToolResult]) -> str:
    return json.dumps(
        [model.model_dump(mode="json") for model in models],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _required_identifier(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} cannot be empty")
    return normalized


def _optional_identifier(value: str | None) -> str | None:
    if value is None:
        return None
    return _required_identifier(value, "conversation_id")


__all__ = ["SQLiteCheckpointStore"]
