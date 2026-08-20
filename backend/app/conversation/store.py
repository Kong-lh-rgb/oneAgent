"""基于 SQLite 的本地会话存储。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import aiosqlite

from app.models.types import Message, ToolCall

from .models import Conversation

DEFAULT_DATABASE_PATH = (
    Path(__file__).resolve().parents[2] / ".oneagent" / "oneagent.db"
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT,
    name TEXT,
    tool_call_id TEXT,
    tool_calls_json TEXT NOT NULL DEFAULT '[]',
    reasoning TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(conversation_id, sequence),
    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation_sequence
ON messages(conversation_id, sequence);

CREATE INDEX IF NOT EXISTS idx_conversations_updated_at
ON conversations(updated_at DESC);
"""


class SQLiteConversationStore:
    """将会话与通用消息保存在单个 SQLite 文件中。"""

    def __init__(self, database_path: str | Path = DEFAULT_DATABASE_PATH) -> None:
        self.database_path = Path(database_path).expanduser().resolve()

    async def initialize(self) -> None:
        """创建数据库目录和数据表（含幂等列迁移）。"""

        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        async with self._connect() as database:
            await database.executescript(_SCHEMA)
            await _ensure_column(database, "messages", "reasoning", "TEXT")
            await database.commit()

    async def create(
        self,
        *,
        title: str = "新会话",
        messages: Sequence[Message] = (),
    ) -> Conversation:
        """创建会话，并可同时写入初始消息。"""

        normalized_title = _normalize_title(title)
        conversation_id = uuid4().hex
        now = _now_iso()
        async with self._connect() as database:
            await database.execute(
                """
                INSERT INTO conversations (id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (conversation_id, normalized_title, now, now),
            )
            await self._insert_messages(database, conversation_id, messages, now)
            await database.commit()

        conversation = await self.get(conversation_id)
        if conversation is None:  # pragma: no cover - SQLite 写入后的防御性检查
            raise RuntimeError("创建会话后无法重新读取会话")
        return conversation

    async def get(self, conversation_id: str) -> Conversation | None:
        """按完整 ID 获取会话。"""

        async with self._connect() as database:
            cursor = await database.execute(
                _CONVERSATION_SELECT + " WHERE c.id = ? GROUP BY c.id",
                (conversation_id,),
            )
            row = await cursor.fetchone()
        return _conversation_from_row(row) if row is not None else None

    async def resolve(self, identifier: str) -> Conversation | None:
        """使用完整 ID 或唯一 ID 前缀查找会话。"""

        normalized = identifier.strip()
        if not normalized:
            return None

        exact = await self.get(normalized)
        if exact is not None:
            return exact

        async with self._connect() as database:
            cursor = await database.execute(
                _CONVERSATION_SELECT
                + " WHERE c.id LIKE ? GROUP BY c.id ORDER BY c.updated_at DESC LIMIT 2",
                (f"{normalized}%",),
            )
            rows = await cursor.fetchall()
        if len(rows) > 1:
            raise ValueError(f"会话 ID 前缀不唯一：{identifier}")
        return _conversation_from_row(rows[0]) if rows else None

    async def latest(self) -> Conversation | None:
        """返回最近更新的会话。"""

        conversations = await self.list(limit=1)
        return conversations[0] if conversations else None

    async def list(self, *, limit: int = 20) -> tuple[Conversation, ...]:
        """按最近更新时间倒序列出会话。"""

        if limit < 1:
            raise ValueError("limit must be at least 1")
        async with self._connect() as database:
            cursor = await database.execute(
                _CONVERSATION_SELECT
                + " GROUP BY c.id ORDER BY c.updated_at DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
        return tuple(_conversation_from_row(row) for row in rows)

    async def load_messages(self, conversation_id: str) -> tuple[Message, ...]:
        """按原始顺序读取会话中的全部消息。"""

        if await self.get(conversation_id) is None:
            raise KeyError(f"会话不存在：{conversation_id}")
        async with self._connect() as database:
            cursor = await database.execute(
                """
                SELECT role, content, name, tool_call_id, tool_calls_json, reasoning
                FROM messages
                WHERE conversation_id = ?
                ORDER BY sequence ASC
                """,
                (conversation_id,),
            )
            rows = await cursor.fetchall()
        return tuple(_message_from_row(row) for row in rows)

    async def replace_messages(
        self,
        conversation_id: str,
        messages: Sequence[Message],
    ) -> Conversation:
        """用完整消息历史替换会话内容。"""

        now = _now_iso()
        async with self._connect() as database:
            cursor = await database.execute(
                "SELECT 1 FROM conversations WHERE id = ?",
                (conversation_id,),
            )
            if await cursor.fetchone() is None:
                raise KeyError(f"会话不存在：{conversation_id}")
            await database.execute(
                "DELETE FROM messages WHERE conversation_id = ?",
                (conversation_id,),
            )
            await self._insert_messages(database, conversation_id, messages, now)
            await database.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )
            await database.commit()

        conversation = await self.get(conversation_id)
        if conversation is None:  # pragma: no cover - SQLite 更新后的防御性检查
            raise RuntimeError("更新会话后无法重新读取会话")
        return conversation

    async def rename(self, conversation_id: str, title: str) -> Conversation:
        """修改会话标题。"""

        now = _now_iso()
        async with self._connect() as database:
            cursor = await database.execute(
                """
                UPDATE conversations
                SET title = ?, updated_at = ?
                WHERE id = ?
                """,
                (_normalize_title(title), now, conversation_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"会话不存在：{conversation_id}")
            await database.commit()

        conversation = await self.get(conversation_id)
        if conversation is None:  # pragma: no cover - SQLite 更新后的防御性检查
            raise RuntimeError("重命名会话后无法重新读取会话")
        return conversation

    async def delete(self, conversation_id: str) -> bool:
        """删除会话及其消息，并返回是否实际删除。"""

        async with self._connect() as database:
            cursor = await database.execute(
                "DELETE FROM conversations WHERE id = ?",
                (conversation_id,),
            )
            await database.commit()
        return cursor.rowcount > 0

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[aiosqlite.Connection]:
        database = await aiosqlite.connect(self.database_path)
        database.row_factory = aiosqlite.Row
        await database.execute("PRAGMA foreign_keys = ON")
        try:
            yield database
        finally:
            await database.close()

    @staticmethod
    async def _insert_messages(
        database: aiosqlite.Connection,
        conversation_id: str,
        messages: Sequence[Message],
        created_at: str,
    ) -> None:
        rows = [
            (
                conversation_id,
                sequence,
                message.role.value,
                message.content,
                message.name,
                message.tool_call_id,
                json.dumps(
                    [
                        tool_call.model_dump(mode="json")
                        for tool_call in message.tool_calls
                    ],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                message.reasoning,
                created_at,
            )
            for sequence, message in enumerate(messages)
        ]
        if rows:
            await database.executemany(
                """
                INSERT INTO messages (
                    conversation_id,
                    sequence,
                    role,
                    content,
                    name,
                    tool_call_id,
                    tool_calls_json,
                    reasoning,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )


_CONVERSATION_SELECT = """
SELECT
    c.id,
    c.title,
    c.created_at,
    c.updated_at,
    COUNT(m.id) AS message_count
FROM conversations AS c
LEFT JOIN messages AS m ON m.conversation_id = c.id
"""


def _conversation_from_row(row: aiosqlite.Row) -> Conversation:
    return Conversation(
        id=row["id"],
        title=row["title"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        message_count=row["message_count"],
    )


async def _ensure_column(
    database: aiosqlite.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    """幂等迁移：表中缺少指定列时追加（用于旧数据库升级）。"""

    cursor = await database.execute(f"PRAGMA table_info({table})")
    columns = {row["name"] for row in await cursor.fetchall()}
    if column not in columns:
        await database.execute(
            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
        )


def _message_from_row(row: aiosqlite.Row) -> Message:
    raw_tool_calls = json.loads(row["tool_calls_json"])
    return Message(
        role=row["role"],
        content=row["content"],
        name=row["name"],
        tool_call_id=row["tool_call_id"],
        tool_calls=tuple(ToolCall.model_validate(item) for item in raw_tool_calls),
        reasoning=row["reasoning"],
    )


def _normalize_title(title: str) -> str:
    normalized = " ".join(title.split()).strip()
    return normalized[:80] or "新会话"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
