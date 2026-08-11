"""SQLite、FTS5 与 sqlite-vec 组成的本地记忆存储。"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import aiosqlite
import sqlite_vec

from app.conversation import DEFAULT_DATABASE_PATH

from .errors import MemoryConflictError, MemoryRevisionConflictError
from .models import (
    MemoryDraft,
    MemoryItem,
    MemoryStatus,
    MemoryType,
    utc_now,
)

_MEMORY_IDENTIFIER_RE = re.compile(r"^[0-9a-f]{4,32}$")


class SQLiteMemoryStore:
    """在一个事务中维护事实行、全文索引和向量索引。"""

    def __init__(
        self,
        database_path: str | Path = DEFAULT_DATABASE_PATH,
        *,
        embedding_dimensions: int = 384,
    ) -> None:
        if embedding_dimensions < 1:
            raise ValueError("embedding_dimensions must be positive")
        self.database_path = Path(database_path).expanduser().resolve()
        self.embedding_dimensions = embedding_dimensions

    async def initialize(self) -> None:
        """加载 sqlite-vec，并创建主表与两个检索索引。"""

        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        async with self._connect() as database:
            await database.executescript(_base_schema())
            await database.execute(
                f"""CREATE VIRTUAL TABLE IF NOT EXISTS memory_vectors
                    USING vec0(memory_id TEXT PRIMARY KEY,
                    embedding float[{self.embedding_dimensions}])"""
            )
            await database.execute(
                "INSERT OR IGNORE INTO memory_meta(key, value) VALUES (?, ?)",
                ("embedding_dimensions", str(self.embedding_dimensions)),
            )
            cursor = await database.execute(
                "SELECT value FROM memory_meta WHERE key = 'embedding_dimensions'"
            )
            stored_dimensions = int((await cursor.fetchone())[0])
            if stored_dimensions != self.embedding_dimensions:
                raise ValueError(
                    "memory embedding dimensions do not match existing database: "
                    f"expected {stored_dimensions}, got {self.embedding_dimensions}"
                )
            await database.commit()

    async def create(
        self,
        draft: MemoryDraft,
        *,
        normalized_content: str,
        fingerprint: str,
        embedding: Sequence[float],
        supersedes_id: str | None = None,
    ) -> MemoryItem:
        """原子写入主表、FTS5 和 vec0。"""

        self._validate_embedding(embedding)
        now = utc_now()
        item = MemoryItem(
            id=uuid4().hex,
            namespace=draft.namespace,
            memory_type=draft.memory_type,
            key=draft.key,
            content=draft.content,
            normalized_content=normalized_content,
            fingerprint=fingerprint,
            status=draft.status,
            importance=draft.importance,
            confidence=draft.confidence,
            source=draft.source,
            supersedes_id=supersedes_id,
            metadata=draft.metadata,
            created_at=now,
            updated_at=now,
        )
        async with self._transaction() as database:
            try:
                await self._insert_database(database, item, embedding)
            except sqlite3.IntegrityError as exc:
                raise MemoryConflictError(
                    "memory fingerprint or active key conflicts"
                ) from exc
        return item

    async def replace(
        self,
        memory_id: str,
        draft: MemoryDraft,
        *,
        normalized_content: str,
        fingerprint: str,
        embedding: Sequence[float],
        expected_revision: int | None = None,
    ) -> tuple[MemoryItem, MemoryItem]:
        """原子停用旧事实并写入新事实。"""

        self._validate_embedding(embedding)
        async with self._transaction() as database:
            current = await self._require_database(database, memory_id)
            _require_revision(current, expected_revision)
            if current.status is not MemoryStatus.ACTIVE:
                raise ValueError("only active memory can be superseded")
            if draft.namespace != current.namespace or draft.key != current.key:
                raise ValueError("replacement must keep namespace and key")
            now = utc_now()
            retired = _copy_item(
                current,
                status=MemoryStatus.SUPERSEDED,
                status_changed_at=now,
                updated_at=now,
                revision=current.revision + 1,
            )
            await self._persist_status(database, retired, current.revision)
            replacement = MemoryItem(
                id=uuid4().hex,
                namespace=draft.namespace,
                memory_type=draft.memory_type,
                key=draft.key,
                content=draft.content,
                normalized_content=normalized_content,
                fingerprint=fingerprint,
                status=draft.status,
                importance=draft.importance,
                confidence=draft.confidence,
                source=draft.source,
                supersedes_id=current.id,
                metadata=draft.metadata,
                created_at=now,
                updated_at=now,
            )
            await self._insert_database(database, replacement, embedding)
        return retired, replacement

    async def get(self, memory_id: str) -> MemoryItem | None:
        async with self._connect() as database:
            cursor = await database.execute(
                _SELECT + " WHERE id = ?",
                (memory_id.strip().lower(),),
            )
            row = await cursor.fetchone()
        return _from_row(row) if row else None

    async def resolve(
        self,
        identifier: str,
        *,
        namespaces: Sequence[str],
    ) -> MemoryItem | None:
        """只在允许的 namespace 中解析完整 ID 或唯一前缀。"""

        normalized = identifier.strip().lower()
        if not _MEMORY_IDENTIFIER_RE.fullmatch(normalized):
            raise ValueError("memory identifier must be 4-32 hexadecimal characters")
        if not namespaces:
            return None
        placeholders = ",".join("?" for _ in namespaces)
        async with self._connect() as database:
            rows = await (
                await database.execute(
                    _SELECT
                    + f""" WHERE namespace IN ({placeholders}) AND id LIKE ?
                        ORDER BY updated_at DESC LIMIT 2""",
                    (*namespaces, f"{normalized}%"),
                )
            ).fetchall()
        if len(rows) > 1:
            raise ValueError(f"记忆 ID 前缀不唯一：{identifier}")
        return _from_row(rows[0]) if rows else None

    async def list(
        self,
        *,
        namespaces: Sequence[str] | None = None,
        statuses: Sequence[MemoryStatus] | None = None,
        memory_type: MemoryType | None = None,
        limit: int = 100,
    ) -> tuple[MemoryItem, ...]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        clauses: list[str] = []
        values: list[object] = []
        _append_in_filter(clauses, values, "namespace", namespaces)
        _append_in_filter(
            clauses,
            values,
            "status",
            [status.value for status in statuses] if statuses else None,
        )
        if memory_type is not None:
            clauses.append("memory_type = ?")
            values.append(memory_type.value)
        statement = _SELECT
        if clauses:
            statement += " WHERE " + " AND ".join(clauses)
        statement += " ORDER BY updated_at DESC LIMIT ?"
        values.append(limit)
        async with self._connect() as database:
            rows = await (await database.execute(statement, values)).fetchall()
        return tuple(_from_row(row) for row in rows)

    async def find_by_fingerprint(
        self,
        fingerprint: str,
    ) -> MemoryItem | None:
        async with self._connect() as database:
            row = await (
                await database.execute(
                    _SELECT
                    + """ WHERE fingerprint = ?
                        AND status IN ('candidate','active') LIMIT 1""",
                    (fingerprint,),
                )
            ).fetchone()
        return _from_row(row) if row else None

    async def find_active_fact(
        self,
        *,
        namespace: str,
        key: str,
    ) -> MemoryItem | None:
        async with self._connect() as database:
            row = await (
                await database.execute(
                    _SELECT
                    + """ WHERE namespace = ? AND memory_type = 'fact'
                        AND memory_key = ? AND status = 'active' LIMIT 1""",
                    (namespace, key),
                )
            ).fetchone()
        return _from_row(row) if row else None

    async def change_status(
        self,
        memory_id: str,
        status: MemoryStatus,
        *,
        expected_revision: int | None = None,
        confirmed: bool = False,
    ) -> MemoryItem:
        if status not in {MemoryStatus.ACTIVE, MemoryStatus.ARCHIVED}:
            raise ValueError("ordinary status change only supports active or archived")
        async with self._transaction() as database:
            current = await self._require_database(database, memory_id)
            _require_revision(current, expected_revision)
            if status is MemoryStatus.ACTIVE:
                if current.status is not MemoryStatus.CANDIDATE:
                    raise ValueError("only candidate memory can be confirmed")
                if current.memory_type is MemoryType.FACT and not current.key:
                    raise ValueError("active FACT memory requires a key")
                conflict = await self._find_active_fact_database(
                    database,
                    current.namespace,
                    current.key,
                )
                if conflict is not None and conflict.id != current.id:
                    raise MemoryConflictError("an active FACT already owns this key")
            elif current.status not in {
                MemoryStatus.CANDIDATE,
                MemoryStatus.ACTIVE,
            }:
                raise ValueError("only candidate or active memory can be archived")
            now = utc_now()
            updated = _copy_item(
                current,
                status=status,
                status_changed_at=(now if status is MemoryStatus.ARCHIVED else None),
                confirmation_count=(
                    current.confirmation_count + 1
                    if status is MemoryStatus.ACTIVE and confirmed
                    else current.confirmation_count
                ),
                updated_at=now,
                revision=current.revision + 1,
            )
            await self._persist_status(database, updated, current.revision)
        return updated

    async def record_access(
        self,
        memory_ids: Sequence[str],
        *,
        used: bool = False,
    ) -> None:
        """记录召回或真实使用；遥测不推进 revision。"""

        if not memory_ids:
            return
        now = _time_text(utc_now())
        placeholders = ",".join("?" for _ in memory_ids)
        async with self._transaction() as database:
            await database.execute(
                f"""UPDATE memories SET access_count = access_count + 1,
                    use_count = use_count + ?, last_accessed_at = ?
                    WHERE id IN ({placeholders})""",
                (int(used), now, *memory_ids),
            )

    async def count_writes(
        self,
        *,
        since: datetime,
        source_session_id: str | None = None,
        source_run_id: str | None = None,
    ) -> int:
        clauses = ["created_at >= ?"]
        values: list[object] = [_time_text(since)]
        if source_session_id is not None:
            clauses.append("source_session_id = ?")
            values.append(source_session_id)
        if source_run_id is not None:
            clauses.append("source_run_id = ?")
            values.append(source_run_id)
        async with self._connect() as database:
            row = await (
                await database.execute(
                    "SELECT COUNT(*) FROM memories WHERE " + " AND ".join(clauses),
                    values,
                )
            ).fetchone()
        return int(row[0])

    async def lexical_search(
        self,
        query: str,
        *,
        namespaces: Sequence[str],
        limit: int = 20,
    ) -> tuple[tuple[MemoryItem, float], ...]:
        expression = _fts_expression(query)
        if not expression or not namespaces:
            return ()
        placeholders = ",".join("?" for _ in namespaces)
        statement = f"""
            SELECT m.*, bm25(memory_fts) AS search_score
            FROM memory_fts JOIN memories m ON m.id = memory_fts.memory_id
            WHERE memory_fts MATCH ? AND m.namespace IN ({placeholders})
              AND m.status = 'active'
            ORDER BY search_score LIMIT ?
        """
        async with self._connect() as database:
            rows = await (
                await database.execute(statement, (expression, *namespaces, limit))
            ).fetchall()
        return tuple((_from_row(row), float(row["search_score"])) for row in rows)

    async def vector_search(
        self,
        embedding: Sequence[float],
        *,
        namespaces: Sequence[str],
        limit: int = 20,
    ) -> tuple[tuple[MemoryItem, float], ...]:
        self._validate_embedding(embedding)
        if not namespaces:
            return ()
        candidate_limit = max(limit * 5, limit)
        async with self._connect() as database:
            vector_rows = await (
                await database.execute(
                    """SELECT memory_id, distance FROM memory_vectors
                       WHERE embedding MATCH ? AND k = ? ORDER BY distance""",
                    (sqlite_vec.serialize_float32(embedding), candidate_limit),
                )
            ).fetchall()
            results: list[tuple[MemoryItem, float]] = []
            for vector_row in vector_rows:
                memory = await self._require_database(
                    database,
                    vector_row["memory_id"],
                )
                is_visible = (
                    memory.status is MemoryStatus.ACTIVE
                    and memory.namespace in namespaces
                )
                if is_visible:
                    results.append((memory, float(vector_row["distance"])))
                    if len(results) >= limit:
                        break
        return tuple(results)

    async def _insert_database(
        self,
        database: aiosqlite.Connection,
        item: MemoryItem,
        embedding: Sequence[float],
    ) -> None:
        await database.execute(
            """INSERT INTO memories (
                id, namespace, memory_type, memory_key, content,
                normalized_content, fingerprint, status, importance, confidence,
                source_session_id, source_run_id, source_message_id, access_count,
                use_count, confirmation_count, last_accessed_at, supersedes_id,
                metadata_json, created_at, updated_at, status_changed_at, revision
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            _item_values(item),
        )
        await database.execute(
            """INSERT INTO memory_fts(memory_id, content, normalized_content)
               VALUES(?,?,?)""",
            (item.id, item.content, item.normalized_content),
        )
        await database.execute(
            "INSERT INTO memory_vectors(memory_id, embedding) VALUES(?,?)",
            (item.id, sqlite_vec.serialize_float32(embedding)),
        )

    async def _persist_status(
        self,
        database: aiosqlite.Connection,
        item: MemoryItem,
        previous_revision: int,
    ) -> None:
        cursor = await database.execute(
            """UPDATE memories SET status=?, confirmation_count=?, updated_at=?,
               status_changed_at=?, revision=? WHERE id=? AND revision=?""",
            (
                item.status.value,
                item.confirmation_count,
                _time_text(item.updated_at),
                _time_text(item.status_changed_at),
                item.revision,
                item.id,
                previous_revision,
            ),
        )
        if cursor.rowcount != 1:
            raise MemoryRevisionConflictError("memory revision changed")

    async def _require_database(
        self,
        database: aiosqlite.Connection,
        memory_id: str,
    ) -> MemoryItem:
        row = await (
            await database.execute(_SELECT + " WHERE id = ?", (memory_id,))
        ).fetchone()
        if row is None:
            raise KeyError(f"记忆不存在：{memory_id}")
        return _from_row(row)

    async def _find_active_fact_database(
        self,
        database: aiosqlite.Connection,
        namespace: str,
        key: str | None,
    ) -> MemoryItem | None:
        if key is None:
            return None
        row = await (
            await database.execute(
                _SELECT
                + """ WHERE namespace=? AND memory_type='fact'
                    AND memory_key=? AND status='active' LIMIT 1""",
                (namespace, key),
            )
        ).fetchone()
        return _from_row(row) if row else None

    def _validate_embedding(self, embedding: Sequence[float]) -> None:
        if len(embedding) != self.embedding_dimensions:
            raise ValueError(
                f"embedding must contain {self.embedding_dimensions} dimensions"
            )

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[aiosqlite.Connection]:
        database = await aiosqlite.connect(self.database_path)
        database.row_factory = aiosqlite.Row
        await database.execute("PRAGMA foreign_keys = ON")
        await database.enable_load_extension(True)
        try:
            await database.load_extension(sqlite_vec.loadable_path())
        except Exception as exc:
            await database.close()
            raise RuntimeError("无法加载 sqlite-vec 扩展") from exc
        finally:
            with suppress(Exception):
                await database.enable_load_extension(False)
        try:
            yield database
        finally:
            await database.close()

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._connect() as database:
            await database.execute("BEGIN IMMEDIATE")
            try:
                yield database
                await database.commit()
            except BaseException:
                await database.rollback()
                raise


def _base_schema() -> str:
    return """
    CREATE TABLE IF NOT EXISTS memory_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS memories(
      id TEXT PRIMARY KEY, namespace TEXT NOT NULL, memory_type TEXT NOT NULL,
      memory_key TEXT, content TEXT NOT NULL, normalized_content TEXT NOT NULL,
      fingerprint TEXT NOT NULL, status TEXT NOT NULL,
      importance REAL NOT NULL, confidence REAL NOT NULL,
      source_session_id TEXT, source_run_id TEXT, source_message_id TEXT,
      access_count INTEGER NOT NULL DEFAULT 0, use_count INTEGER NOT NULL DEFAULT 0,
      confirmation_count INTEGER NOT NULL DEFAULT 0, last_accessed_at TEXT,
      supersedes_id TEXT, metadata_json TEXT NOT NULL DEFAULT '{}',
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL, status_changed_at TEXT,
      revision INTEGER NOT NULL, FOREIGN KEY(supersedes_id) REFERENCES memories(id)
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_active_fact_key
      ON memories(namespace, memory_key)
      WHERE memory_type='fact' AND status='active' AND memory_key IS NOT NULL;
    CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_live_fingerprint
      ON memories(fingerprint) WHERE status IN ('candidate','active');
    CREATE INDEX IF NOT EXISTS idx_memory_retrieval
      ON memories(namespace, status, updated_at DESC);
    CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
      memory_id UNINDEXED, content, normalized_content, tokenize='unicode61'
    );
    """


_SELECT = "SELECT * FROM memories"


def _item_values(item: MemoryItem) -> tuple[object, ...]:
    return (
        item.id, item.namespace, item.memory_type.value, item.key, item.content,
        item.normalized_content, item.fingerprint, item.status.value,
        item.importance, item.confidence, item.source.session_id, item.source.run_id,
        item.source.message_id, item.access_count, item.use_count,
        item.confirmation_count, _time_text(item.last_accessed_at),
        item.supersedes_id, json.dumps(item.metadata, ensure_ascii=False),
        _time_text(item.created_at), _time_text(item.updated_at),
        _time_text(item.status_changed_at), item.revision,
    )


def _from_row(row: aiosqlite.Row) -> MemoryItem:
    from .models import MemorySource

    return MemoryItem(
        id=row["id"],
        namespace=row["namespace"],
        memory_type=row["memory_type"],
        key=row["memory_key"],
        content=row["content"],
        normalized_content=row["normalized_content"],
        fingerprint=row["fingerprint"],
        status=row["status"],
        importance=row["importance"],
        confidence=row["confidence"],
        source=MemorySource(
            session_id=row["source_session_id"],
            run_id=row["source_run_id"],
            message_id=row["source_message_id"],
        ),
        access_count=row["access_count"],
        use_count=row["use_count"],
        confirmation_count=row["confirmation_count"],
        last_accessed_at=_parse_time(row["last_accessed_at"]),
        supersedes_id=row["supersedes_id"],
        metadata=json.loads(row["metadata_json"]),
        created_at=_parse_time(row["created_at"]),
        updated_at=_parse_time(row["updated_at"]),
        status_changed_at=_parse_time(row["status_changed_at"]),
        revision=row["revision"],
    )


def _copy_item(item: MemoryItem, **updates: object) -> MemoryItem:
    values = item.model_dump()
    values.update(updates)
    return MemoryItem.model_validate(values)


def _require_revision(item: MemoryItem, expected: int | None) -> None:
    if expected is not None and item.revision != expected:
        raise MemoryRevisionConflictError(
            f"memory revision conflict: expected {expected}, current {item.revision}"
        )


def _append_in_filter(
    clauses: list[str],
    values: list[object],
    column: str,
    selected: Sequence[str] | None,
) -> None:
    if selected:
        clauses.append(f"{column} IN ({','.join('?' for _ in selected)})")
        values.extend(selected)


def _fts_expression(query: str) -> str:
    tokens = [token.replace('"', '""') for token in query.split() if token]
    return " OR ".join(f'"{token}"' for token in tokens[:20])


def _time_text(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


__all__ = ["SQLiteMemoryStore"]
