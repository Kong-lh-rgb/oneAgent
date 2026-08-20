"""Artifact metadata 的 Durable SQLite store。

只存 metadata（immutable）；文件内容由 ArtifactService 管理在
``database.parent/artifacts/<artifact_id>/<safe_filename>``。
V1 不做 delete / update / versioning。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from app.conversation import DEFAULT_DATABASE_PATH

from .models import Artifact, ArtifactKind

_SCHEMA = """
CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    description TEXT,
    filename TEXT,
    mime_type TEXT,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    sha256 TEXT,
    run_id TEXT,
    conversation_id TEXT,
    task_id TEXT,
    source_url TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_artifacts_created
ON artifacts(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_artifacts_run_id
ON artifacts(run_id);

CREATE INDEX IF NOT EXISTS idx_artifacts_conversation_id
ON artifacts(conversation_id);
"""


class SQLiteArtifactStore:
    """持久化 Artifact 元数据（与 Run / Approval / Trace 共用同一数据库文件）。"""

    def __init__(self, database_path: str | Path = DEFAULT_DATABASE_PATH) -> None:
        self.database_path = Path(database_path).expanduser().resolve()

    async def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        async with self._connect() as database:
            await database.executescript(_SCHEMA)
            await database.commit()

    async def create(self, artifact: Artifact) -> Artifact:
        """写入一条不可变 Artifact 元数据。"""

        async with self._connect() as database:
            await database.execute(
                """
                INSERT INTO artifacts (
                    id, kind, title, description, filename, mime_type,
                    size_bytes, sha256, run_id, conversation_id, task_id,
                    source_url, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact.id,
                    artifact.kind.value,
                    artifact.title,
                    artifact.description,
                    artifact.filename,
                    artifact.mime_type,
                    artifact.size_bytes,
                    artifact.sha256,
                    artifact.run_id,
                    artifact.conversation_id,
                    artifact.task_id,
                    artifact.source_url,
                    artifact.created_at.isoformat(),
                ),
            )
            await database.commit()
        return artifact

    async def get(self, artifact_id: str) -> Artifact | None:
        async with self._connect() as database:
            async with database.execute(
                "SELECT * FROM artifacts WHERE id = ?", (artifact_id,)
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_artifact(row)

    async def list(
        self,
        *,
        run_id: str | None = None,
        conversation_id: str | None = None,
        limit: int = 50,
    ) -> tuple[Artifact, ...]:
        clauses: list[str] = []
        params: list[Any] = []
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if conversation_id:
            clauses.append("conversation_id = ?")
            params.append(conversation_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(int(limit))
        async with self._connect() as database:
            async with database.execute(
                f"""
                SELECT * FROM artifacts
                {where}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                tuple(params),
            ) as cursor:
                rows = await cursor.fetchall()
        return tuple(_row_to_artifact(row) for row in rows)

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[aiosqlite.Connection]:
        connection = await aiosqlite.connect(self.database_path)
        connection.row_factory = aiosqlite.Row
        try:
            yield connection
        finally:
            await connection.close()


def _row_to_artifact(row: aiosqlite.Row) -> Artifact:
    created_at = row["created_at"]
    created = (
        datetime.fromisoformat(created_at).astimezone(UTC)
        if created_at
        else datetime.now(UTC)
    )
    return Artifact(
        id=row["id"],
        kind=ArtifactKind(row["kind"]),
        title=row["title"] or "",
        description=row["description"],
        filename=row["filename"],
        mime_type=row["mime_type"],
        size_bytes=row["size_bytes"] or 0,
        sha256=row["sha256"],
        run_id=row["run_id"],
        conversation_id=row["conversation_id"],
        task_id=row["task_id"],
        source_url=row["source_url"],
        created_at=created,
    )
