"""基于现有 OneAgent SQLite 文件的 Run 生命周期存储。

与 CheckpointStore / TraceStore 共用同一个数据库文件（默认
``backend/.oneagent/oneagent.db``），只新增一张 ``runs`` 表，不引入新的
数据库体系。所有写入使用 ``BEGIN IMMEDIATE`` 原子事务，非法状态转换被拒绝。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import aiosqlite

from app.conversation import DEFAULT_DATABASE_PATH

from .models import (
    _ALLOWED_TRANSITIONS,
    TERMINAL_STATUSES,
    Run,
    RunStatus,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    conversation_id TEXT,
    status TEXT NOT NULL,
    user_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    started_at TEXT,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    error TEXT,
    stop_reason TEXT,
    recovered_from_run_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_conversation_updated
ON runs(conversation_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_runs_status_updated
ON runs(status, created_at DESC);
"""


class SQLiteRunStore:
    """持久化 Run 生命周期记录，并强制状态转换合法性。"""

    def __init__(self, database_path: str | Path = DEFAULT_DATABASE_PATH) -> None:
        self.database_path = Path(database_path).expanduser().resolve()

    async def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        async with self._connect() as database:
            await database.executescript(_SCHEMA)
            await database.commit()

    async def create(
        self,
        *,
        conversation_id: str | None = None,
        user_message: str = "",
        recovered_from_run_id: str | None = None,
    ) -> Run:
        """创建一个 PENDING Run，并返回完整记录。"""

        run_id = uuid4().hex
        now = _now()
        async with self._connect() as database:
            await database.execute("BEGIN IMMEDIATE")
            await database.execute(
                """
                INSERT INTO runs (
                    run_id, conversation_id, status, user_message,
                    created_at, updated_at, recovered_from_run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    _optional_identifier(conversation_id),
                    RunStatus.PENDING.value,
                    user_message,
                    now,
                    now,
                    _optional_identifier(recovered_from_run_id),
                ),
            )
            await database.commit()
        run = await self.require(run_id)
        return run

    async def get(self, run_id: str) -> Run | None:
        async with self._connect() as database:
            cursor = await database.execute(
                "SELECT * FROM runs WHERE run_id = ?",
                (_required_identifier(run_id, "run_id"),),
            )
            row = await cursor.fetchone()
        return _run_from_row(row) if row is not None else None

    async def list_runs(
        self,
        *,
        conversation_id: str | None = None,
        status: RunStatus | str | None = None,
        limit: int = 20,
    ) -> tuple[Run, ...]:
        """按创建时间倒序列出 Run；支持按会话 / 状态简单过滤。"""

        if limit < 1:
            raise ValueError("limit must be at least 1")
        clauses: list[str] = []
        parameters: list[object] = []
        if conversation_id is not None:
            clauses.append("conversation_id = ?")
            parameters.append(_required_identifier(conversation_id, "conversation_id"))
        if status is not None:
            clauses.append("status = ?")
            parameters.append(RunStatus(status).value)
        query = "SELECT * FROM runs"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT ?"
        parameters.append(limit)

        async with self._connect() as database:
            cursor = await database.execute(query, tuple(parameters))
            rows = await cursor.fetchall()
        return tuple(_run_from_row(row) for row in rows)

    async def update_status(
        self,
        run_id: str,
        status: RunStatus,
        *,
        error: str | None = None,
        stop_reason: str | None = None,
    ) -> Run:
        """原子状态转换；非法转换抛 ValueError，终态不可再转换。"""

        if not isinstance(status, RunStatus):
            status = RunStatus(status)
        async with self._connect() as database:
            await database.execute("BEGIN IMMEDIATE")
            current = await _require_row(database, run_id)
            allowed = _ALLOWED_TRANSITIONS[current.status]
            if status not in allowed:
                raise ValueError(
                    f"invalid run transition: {current.status.value} -> "
                    f"{status.value}"
                )
            now = _now()
            started_at = (
                current.started_at
                if current.started_at is not None
                else (now if status is RunStatus.RUNNING else None)
            )
            completed_at = (
                now
                if status in TERMINAL_STATUSES
                else None
            )
            await database.execute(
                """
                UPDATE runs
                SET status = ?, started_at = COALESCE(?, started_at),
                    updated_at = ?, completed_at = ?, error = ?, stop_reason = ?
                WHERE run_id = ?
                """,
                (
                    status.value,
                    started_at,
                    now,
                    completed_at,
                    error,
                    stop_reason,
                    run_id,
                ),
            )
            await database.commit()
        return await self.require(run_id)

    async def mark_started(self, run_id: str) -> Run:
        """RUNNING（仅允许 PENDING → RUNNING）。"""

        return await self.update_status(run_id, RunStatus.RUNNING)

    async def mark_completed(
        self,
        run_id: str,
        *,
        stop_reason: str | None = None,
    ) -> Run:
        return await self.update_status(
            run_id,
            RunStatus.COMPLETED,
            stop_reason=stop_reason,
        )

    async def mark_failed(
        self,
        run_id: str,
        *,
        error: str | None = None,
        stop_reason: str | None = None,
    ) -> Run:
        return await self.update_status(
            run_id,
            RunStatus.FAILED,
            error=error,
            stop_reason=stop_reason,
        )

    async def mark_cancelled(
        self,
        run_id: str,
        *,
        error: str | None = None,
    ) -> Run:
        return await self.update_status(
            run_id,
            RunStatus.CANCELLED,
            error=error,
        )

    async def mark_interrupted(
        self,
        run_id: str,
        *,
        error: str | None = None,
    ) -> Run:
        return await self.update_status(
            run_id,
            RunStatus.INTERRUPTED,
            error=error,
        )

    async def require(self, run_id: str) -> Run:
        run = await self.get(run_id)
        if run is None:
            raise KeyError(f"Run 不存在：{run_id}")
        return run

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[aiosqlite.Connection]:
        database = await aiosqlite.connect(self.database_path)
        database.row_factory = aiosqlite.Row
        try:
            yield database
        finally:
            await database.close()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _required_identifier(value: str, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} cannot be empty")
    return normalized


def _optional_identifier(value: str | None) -> str | None:
    if value is None:
        return None
    return _required_identifier(value, "identifier")


async def _require_row(database: aiosqlite.Connection, run_id: str) -> Run:
    cursor = await database.execute(
        "SELECT * FROM runs WHERE run_id = ?",
        (_required_identifier(run_id, "run_id"),),
    )
    row = await cursor.fetchone()
    if row is None:
        raise KeyError(f"Run 不存在：{run_id}")
    return _run_from_row(row)


def _run_from_row(row: aiosqlite.Row) -> Run:
    return Run(
        id=row["run_id"],
        conversation_id=row["conversation_id"],
        status=RunStatus(row["status"]),
        user_message=row["user_message"] or "",
        created_at=_parse_datetime(row["created_at"]),
        started_at=(
            _parse_datetime(row["started_at"])
            if row["started_at"] is not None
            else None
        ),
        updated_at=_parse_datetime(row["updated_at"]),
        completed_at=(
            _parse_datetime(row["completed_at"])
            if row["completed_at"] is not None
            else None
        ),
        error=row["error"],
        stop_reason=row["stop_reason"],
        recovered_from_run_id=row["recovered_from_run_id"],
    )


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


__all__ = ["SQLiteRunStore"]
