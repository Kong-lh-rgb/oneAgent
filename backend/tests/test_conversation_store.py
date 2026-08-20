from __future__ import annotations

import aiosqlite
import pytest

from app.conversation import SQLiteConversationStore
from app.models.types import Message, MessageRole, ToolCall


@pytest.mark.asyncio
async def test_reasoning_persists_across_store_restart(tmp_path) -> None:
    database_path = tmp_path / "vesta.db"
    store = SQLiteConversationStore(database_path)
    await store.initialize()
    messages = (
        Message(role=MessageRole.USER, content="分析一下"),
        Message(
            role=MessageRole.ASSISTANT,
            content="结论",
            reasoning="先拆解问题，再对比方案",
        ),
    )
    conversation = await store.create(title="推理", messages=messages)

    reopened_store = SQLiteConversationStore(database_path)
    await reopened_store.initialize()
    restored = await reopened_store.load_messages(conversation.id)
    assert restored[1].content == "结论"
    assert restored[1].reasoning == "先拆解问题，再对比方案"


@pytest.mark.asyncio
async def test_initialize_migrates_legacy_messages_table(tmp_path) -> None:
    """旧库 messages 表没有 reasoning 列时，initialize() 应幂等补列。"""
    database_path = tmp_path / "legacy.db"
    async with aiosqlite.connect(database_path) as db:
        await db.executescript(
            """
            CREATE TABLE conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                name TEXT,
                tool_call_id TEXT,
                tool_calls_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            );
            """
        )
        await db.commit()

    store = SQLiteConversationStore(database_path)
    await store.initialize()

    conversation = await store.create(
        title="迁移",
        messages=(
            Message(
                role=MessageRole.ASSISTANT,
                content="ok",
                reasoning="迁移后仍保留思考",
            ),
        ),
    )
    restored = await store.load_messages(conversation.id)
    assert restored[0].reasoning == "迁移后仍保留思考"


@pytest.mark.asyncio
async def test_conversation_messages_survive_store_restart(tmp_path) -> None:
    database_path = tmp_path / "vesta.db"
    store = SQLiteConversationStore(database_path)
    await store.initialize()
    messages = (
        Message(role=MessageRole.SYSTEM, content="你是本地助理。"),
        Message(role=MessageRole.USER, content="读取文件"),
        Message(
            role=MessageRole.ASSISTANT,
            tool_calls=(
                ToolCall(
                    id="call-1",
                    name="read_file",
                    arguments={"path": "hello.txt"},
                ),
            ),
        ),
        Message(
            role=MessageRole.TOOL,
            name="read_file",
            tool_call_id="call-1",
            content='{"success":true,"output":"你好"}',
        ),
        Message(role=MessageRole.ASSISTANT, content="文件内容是：你好"),
    )
    conversation = await store.create(title="文件问答", messages=messages)

    reopened_store = SQLiteConversationStore(database_path)
    await reopened_store.initialize()
    restored = await reopened_store.get(conversation.id)

    assert restored is not None
    assert restored.title == "文件问答"
    assert restored.message_count == len(messages)
    assert await reopened_store.load_messages(conversation.id) == messages


@pytest.mark.asyncio
async def test_latest_list_rename_and_prefix_resolution(tmp_path) -> None:
    store = SQLiteConversationStore(tmp_path / "vesta.db")
    await store.initialize()
    first = await store.create(title="第一个会话")
    second = await store.create(title="第二个会话")
    renamed = await store.rename(first.id, "  更新后的   会话标题  ")

    assert renamed.title == "更新后的 会话标题"
    assert await store.latest() == renamed
    assert await store.resolve(first.id[:8]) == renamed
    assert await store.resolve("missing") is None
    assert {item.id for item in await store.list()} == {first.id, second.id}


@pytest.mark.asyncio
async def test_replace_messages_and_delete_conversation(tmp_path) -> None:
    store = SQLiteConversationStore(tmp_path / "vesta.db")
    await store.initialize()
    conversation = await store.create(
        messages=(Message(role=MessageRole.USER, content="旧消息"),)
    )
    replacement = (
        Message(role=MessageRole.SYSTEM, content="系统消息"),
        Message(role=MessageRole.USER, content="新消息"),
    )

    updated = await store.replace_messages(conversation.id, replacement)

    assert updated.message_count == 2
    assert await store.load_messages(conversation.id) == replacement
    assert await store.delete(conversation.id) is True
    assert await store.delete(conversation.id) is False
    assert await store.get(conversation.id) is None
    with pytest.raises(KeyError, match="会话不存在"):
        await store.load_messages(conversation.id)


@pytest.mark.asyncio
async def test_missing_conversation_cannot_be_updated(tmp_path) -> None:
    store = SQLiteConversationStore(tmp_path / "vesta.db")
    await store.initialize()

    with pytest.raises(KeyError, match="会话不存在"):
        await store.replace_messages("missing", ())
    with pytest.raises(KeyError, match="会话不存在"):
        await store.rename("missing", "标题")
