"""基于现有 OneAgent SQLite 文件的 ApprovalRequest 持久化。

与 RunStore / CheckpointStore / TraceStore 共用同一个数据库文件（默认
``backend/.oneagent/oneagent.db``），只新增一张 ``approvals`` 表。所有写入使用
``BEGIN IMMEDIATE`` 原子事务；resolve 只在 PENDING 时可执行一次（并发安全），
已 resolved 的记录不能再修改。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiosqlite

from app.conversation import DEFAULT_DATABASE_PATH

from .models import ApprovalRequest, ApprovalRequestStatus

_SCHEMA = """
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    run_id TEXT,
    conversation_id TEXT,
    tool_name TEXT NOT NULL,
    tool_call_id TEXT NOT NULL,
    arguments_json TEXT NOT NULL DEFAULT '{}',
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_approvals_status_created
ON approvals(status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_approvals_run_id
ON approvals(run_id);
"""


class SQLiteApprovalStore:
    """持久化 ApprovalRequest，并保证 approve / deny 只能执行一次。"""

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
        run_id: str | None,
        conversation_id: str | None,
        tool_name: str,
        tool_call_id: str,
        arguments: dict[str, Any] | None = None,
        reason: str = "",
    ) -> ApprovalRequest:
        """创建一个 PENDING ApprovalRequest 并返回完整记录。"""

        approval_id = uuid4().hex
        now = _now()
        arguments = arguments or {}
        async with self._connect() as database:
            await database.execute("BEGIN IMMEDIATE")
            await database.execute(
                """
                INSERT INTO approvals (
                    id, run_id, conversation_id, tool_name, tool_call_id,
                    arguments_json, reason, status, created_at, resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval_id,
                    _optional_identifier(run_id),
                    _optional_identifier(conversation_id),
                    _required(tool_name, "tool_name"),
                    _required(tool_call_id, "tool_call_id"),
                    json.dumps(
                        arguments,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    reason or "",
                    ApprovalRequestStatus.PENDING.value,
                    now,
                    None,
                ),
            )
            await database.commit()
        return await self.require(approval_id)

    async def get(self, approval_id: str) -> ApprovalRequest | None:
        async with self._connect() as database:
            cursor = await database.execute(
                "SELECT * FROM approvals WHERE id = ?",
                (_required(approval_id, "approval_id"),),
            )
            row = await cursor.fetchone()
        return _approval_from_row(row) if row is not None else None

    async def require(self, approval_id: str) -> ApprovalRequest:
        approval = await self.get(approval_id)
        if approval is None:
            raise KeyError(f"ApprovalRequest 不存在：{approval_id}")
        return approval

    async def list(
        self,
        *,
        status: ApprovalRequestStatus | str | None = None,
        run_id: str | None = None,
        conversation_id: str | None = None,
        limit: int = 50,
    ) -> tuple[ApprovalRequest, ...]:
        """按创建时间倒序列出审批记录；支持按状态 / Run / 会话过滤。"""

        if limit < 1:
            raise ValueError("limit must be at least 1")
        clauses: list[str] = []
        parameters: list[object] = []
        if status is not None:
            clauses.append("status = ?")
            parameters.append(ApprovalRequestStatus(status).value)
        if run_id is not None:
            clauses.append("run_id = ?")
            parameters.append(_required(run_id, "run_id"))
        if conversation_id is not None:
            clauses.append("conversation_id = ?")
            parameters.append(_required(conversation_id, "conversation_id"))
        query = "SELECT * FROM approvals"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT ?"
        parameters.append(limit)

        async with self._connect() as database:
            cursor = await database.execute(query, tuple(parameters))
            rows = await cursor.fetchall()
        return tuple(_approval_from_row(row) for row in rows)

    async def resolve(
        self,
        approval_id: str,
        status: ApprovalRequestStatus | str,
    ) -> ApprovalRequest:
        """把 PENDING 审批原子地置为 APPROVED / DENIED（只能执行一次）。

        - 已 resolved（APPROVED / DENIED）的记录再调用会抛 ValueError；
        - 状态写入与读取在同一 ``BEGIN IMMEDIATE`` 事务内，并发下仍保证
          只有一个调用能真正 resolve。
        """

        resolved = ApprovalRequestStatus(status)
        if resolved is ApprovalRequestStatus.PENDING:
            raise ValueError("cannot resolve approval back to PENDING")
        async with self._connect() as database:
            await database.execute("BEGIN IMMEDIATE")
            current = await _require_row(database, approval_id)
            if current.status is not ApprovalRequestStatus.PENDING:
                raise ValueError(
                    f"approval already resolved: {approval_id} "
                    f"({current.status.value})"
                )
            now = _now()
            await database.execute(
                """
                UPDATE approvals
                SET status = ?, resolved_at = ?
                WHERE id = ?
                """,
                (resolved.value, now, approval_id),
            )
            await database.commit()
        return await self.require(approval_id)

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


def _required(value: str, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} cannot be empty")
    return normalized


def _optional_identifier(value: str | None) -> str | None:
    if value is None:
        return None
    return _required(value, "identifier")


async def _require_row(
    database: aiosqlite.Connection,
    approval_id: str,
) -> ApprovalRequest:
    cursor = await database.execute(
        "SELECT * FROM approvals WHERE id = ?",
        (_required(approval_id, "approval_id"),),
    )
    row = await cursor.fetchone()
    if row is None:
        raise KeyError(f"ApprovalRequest 不存在：{approval_id}")
    return _approval_from_row(row)


def _approval_from_row(row: aiosqlite.Row) -> ApprovalRequest:
    return ApprovalRequest(
        id=row["id"],
        run_id=row["run_id"],
        conversation_id=row["conversation_id"],
        tool_name=row["tool_name"],
        tool_call_id=row["tool_call_id"],
        arguments=json.loads(row["arguments_json"] or "{}"),
        reason=row["reason"] or "",
        status=ApprovalRequestStatus(row["status"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        resolved_at=(
            datetime.fromisoformat(row["resolved_at"])
            if row["resolved_at"] is not None
            else None
        ),
    )


__all__ = ["SQLiteApprovalStore"]
