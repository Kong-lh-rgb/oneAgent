"""Automation 的 SQLite 持久化存储。

与 Conversation / Checkpoint / Trace / Run 共用同一个 ``oneagent.db``，
只新增一张 ``automations`` 表，不引入新的数据库体系。写入使用事务。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import aiosqlite

from app.conversation import DEFAULT_DATABASE_PATH

from .models import Automation, AutomationStatus, Schedule

_SCHEMA = """
CREATE TABLE IF NOT EXISTS automations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    prompt TEXT NOT NULL,
    conversation_id TEXT,
    status TEXT NOT NULL,
    schedule_json TEXT NOT NULL,
    next_run_at TEXT,
    last_run_at TEXT,
    last_run_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_automations_status_updated
ON automations(status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_automations_conversation
ON automations(conversation_id, updated_at DESC);
"""


class SQLiteAutomationStore:
    """持久化 Automation，支持按状态 / 会话简单过滤。"""

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
        title: str,
        prompt: str,
        conversation_id: str | None,
        schedule: Schedule,
        next_run_at: datetime,
    ) -> Automation:
        """创建一个 ACTIVE Automation。"""

        automation_id = uuid4().hex
        now = _now()
        async with self._connect() as database:
            await database.execute("BEGIN IMMEDIATE")
            await database.execute(
                """
                INSERT INTO automations (
                    id, title, prompt, conversation_id, status, schedule_json,
                    next_run_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    automation_id,
                    title,
                    prompt,
                    _optional_identifier(conversation_id),
                    AutomationStatus.ACTIVE.value,
                    schedule.model_dump_json(),
                    next_run_at.astimezone(UTC).isoformat(),
                    now,
                    now,
                ),
            )
            await database.commit()
        automation = await self.require(automation_id)
        return automation

    async def get(self, automation_id: str) -> Automation | None:
        async with self._connect() as database:
            cursor = await database.execute(
                "SELECT * FROM automations WHERE id = ?",
                (_required_identifier(automation_id, "automation_id"),),
            )
            row = await cursor.fetchone()
        return _automation_from_row(row) if row is not None else None

    async def resolve(self, identifier: str) -> Automation | None:
        """按完整 ID 或唯一前缀查找。"""

        normalized = identifier.strip()
        if not normalized:
            return None
        exact = await self.get(normalized)
        if exact is not None:
            return exact
        async with self._connect() as database:
            cursor = await database.execute(
                """
                SELECT * FROM automations
                WHERE id LIKE ?
                ORDER BY updated_at DESC LIMIT 2
                """,
                (f"{normalized}%",),
            )
            rows = await cursor.fetchall()
        if len(rows) > 1:
            raise ValueError(f"Automation ID 前缀不唯一：{identifier}")
        return _automation_from_row(rows[0]) if rows else None

    async def list(
        self,
        *,
        status: AutomationStatus | str | None = None,
        conversation_id: str | None = None,
        limit: int = 50,
    ) -> tuple[Automation, ...]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        clauses: list[str] = []
        parameters: list[object] = []
        if status is not None:
            clauses.append("status = ?")
            parameters.append(AutomationStatus(status).value)
        if conversation_id is not None:
            clauses.append("conversation_id = ?")
            parameters.append(_required_identifier(conversation_id, "conversation_id"))
        query = "SELECT * FROM automations"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY updated_at DESC LIMIT ?"
        parameters.append(limit)

        async with self._connect() as database:
            cursor = await database.execute(query, tuple(parameters))
            rows = await cursor.fetchall()
        return tuple(_automation_from_row(row) for row in rows)

    async def update_status(
        self,
        automation_id: str,
        status: AutomationStatus,
        *,
        next_run_at: datetime | None = None,
    ) -> Automation:
        """更新状态（可一并更新下一次触发时间）。"""

        async with self._connect() as database:
            await database.execute("BEGIN IMMEDIATE")
            current = await _require_row(database, automation_id)
            if current.status is status:
                pass  # 允许幂等
            await database.execute(
                """
                UPDATE automations
                SET status = ?, next_run_at = COALESCE(?, next_run_at),
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    status.value,
                    (
                        next_run_at.astimezone(UTC).isoformat()
                        if next_run_at is not None
                        else None
                    ),
                    _now(),
                    automation_id,
                ),
            )
            await database.commit()
        return await self.require(automation_id)

    async def mark_triggered(
        self,
        automation_id: str,
        *,
        last_run_id: str,
        last_run_at: datetime,
        next_run_at: datetime | None = None,
    ) -> Automation:
        """记录一次触发：更新 last_run_id / last_run_at / next_run_at。"""

        async with self._connect() as database:
            await database.execute("BEGIN IMMEDIATE")
            await _require_row(database, automation_id)
            await database.execute(
                """
                UPDATE automations
                SET last_run_id = ?, last_run_at = ?,
                    next_run_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    _required_identifier(last_run_id, "last_run_id"),
                    last_run_at.astimezone(UTC).isoformat(),
                    (
                        next_run_at.astimezone(UTC).isoformat()
                        if next_run_at is not None
                        else None
                    ),
                    _now(),
                    automation_id,
                ),
            )
            await database.commit()
        return await self.require(automation_id)

    async def require(self, automation_id: str) -> Automation:
        automation = await self.get(automation_id)
        if automation is None:
            raise KeyError(f"Automation 不存在：{automation_id}")
        return automation

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


async def _require_row(
    database: aiosqlite.Connection,
    automation_id: str,
) -> Automation:
    cursor = await database.execute(
        "SELECT * FROM automations WHERE id = ?",
        (_required_identifier(automation_id, "automation_id"),),
    )
    row = await cursor.fetchone()
    if row is None:
        raise KeyError(f"Automation 不存在：{automation_id}")
    return _automation_from_row(row)


def _automation_from_row(row: aiosqlite.Row) -> Automation:
    return Automation(
        id=row["id"],
        title=row["title"] or "",
        prompt=row["prompt"],
        conversation_id=row["conversation_id"],
        status=AutomationStatus(row["status"]),
        schedule=Schedule.model_validate_json(row["schedule_json"]),
        next_run_at=(
            _parse_datetime(row["next_run_at"])
            if row["next_run_at"] is not None
            else None
        ),
        last_run_at=(
            _parse_datetime(row["last_run_at"])
            if row["last_run_at"] is not None
            else None
        ),
        last_run_id=row["last_run_id"],
        created_at=_parse_datetime(row["created_at"]),
        updated_at=_parse_datetime(row["updated_at"]),
    )


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


__all__ = ["SQLiteAutomationStore"]
