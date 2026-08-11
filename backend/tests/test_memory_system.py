"""Sparse, Model-Directed 长期记忆系统的离线测试。

覆盖：Core Memory、Memory CRUD、运行时元数据、INDEX 投影、容量维护、
Runtime 注入、Memory Policy 与语义工具。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.memory import (
    CORE_MEMORY_MESSAGE_NAME,
    MEMORY_INDEX_MESSAGE_NAME,
    MEMORY_POLICY_MESSAGE_NAME,
    MEMORY_POLICY_PROMPT,
    MemoryManager,
    MemoryRecord,
    MemoryStatus,
    register_memory_tools,
    register_memory_write_tools,
)
from app.models.types import ToolCall
from app.tools.hooks import ToolExecutionContext
from app.tools.registry import ToolRegistry


@pytest.fixture
def memory_root(tmp_path: Path) -> Path:
    return tmp_path / "memory"


async def _manager(root: Path) -> MemoryManager:
    manager = MemoryManager(root)
    await manager.initialize()
    return manager


# ----------------------------------------------------------------------
# Core Memory
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_core_loads_empty_when_missing(memory_root: Path) -> None:
    manager = await _manager(memory_root)

    assert await manager.core.load() == ""


@pytest.mark.asyncio
async def test_core_update_and_load_roundtrip(memory_root: Path) -> None:
    manager = await _manager(memory_root)

    await manager.core.update("用户长期偏好：使用中文交流。")

    assert "使用中文交流" in await manager.core.load()


@pytest.mark.asyncio
async def test_core_upsert_preserves_legacy_and_other_entries(
    memory_root: Path,
) -> None:
    manager = await _manager(memory_root)
    await manager.core.update("# Core Memory\n\n人工维护的长期约束。")

    first, first_created = await manager.upsert_core(
        key="communication.language",
        value="始终使用中文交流。",
        reason="用户明确表达长期语言偏好",
        source_statement="以后都使用中文和我交流",
    )
    _, second_created = await manager.upsert_core(
        key="code.comment_language",
        value="代码注释使用中文。",
        reason="用户明确表达长期代码约束",
        source_statement="以后代码注释都使用中文",
    )
    updated, created_again = await manager.upsert_core(
        key="communication.language",
        value="默认使用简体中文交流。",
        reason="用户更新了语言偏好",
        source_statement="以后默认使用简体中文",
    )

    visible = await manager.core.load()
    raw = (memory_root / "CORE.md").read_text(encoding="utf-8")
    assert first.key == "communication.language"
    assert first_created is True
    assert second_created is True
    assert created_again is False
    assert updated.value == "默认使用简体中文交流。"
    assert "人工维护的长期约束" in visible
    assert "默认使用简体中文交流" in visible
    assert "代码注释使用中文" in visible
    assert "始终使用中文交流" not in visible
    assert "用户更新了语言偏好" in raw
    assert "以后默认使用简体中文" in raw
    assert "用户更新了语言偏好" not in visible
    assert "以后默认使用简体中文" not in visible


@pytest.mark.asyncio
async def test_core_upsert_failure_does_not_change_file(memory_root: Path) -> None:
    manager = MemoryManager(memory_root, max_core_tokens=20)
    await manager.initialize()
    await manager.upsert_core(
        key="communication.language",
        value="中文",
        reason="用户明确要求",
        source_statement="以后使用中文",
    )
    before = (memory_root / "CORE.md").read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="core memory exceeds token limit"):
        await manager.upsert_core(
            key="identity.background",
            value="非常长的身份信息" * 100,
            reason="用户明确更新身份",
            source_statement="我的身份背景已经更新",
        )

    assert (memory_root / "CORE.md").read_text(encoding="utf-8") == before


@pytest.mark.asyncio
async def test_core_does_not_count_towards_active_limit(memory_root: Path) -> None:
    manager = await _manager(memory_root)
    await manager.core.update("# Core Memory\n\n用户身份：开发者")

    for index in range(25):
        await manager.create(
            title=f"记忆 {index}",
            summary=f"cue {index}",
            content=f"内容 {index}",
        )

    # 25 条 active 不触发维护；Core 不计入上限。
    assert await manager.store.count_active() == 25
    assert await manager.maintenance_required() is False


@pytest.mark.asyncio
async def test_core_not_archived_with_memories(memory_root: Path) -> None:
    manager = await _manager(memory_root)
    await manager.core.update("用户身份：开发者")
    record = await manager.create(
        title="临时记忆",
        summary="cue",
        content="内容",
    )

    await manager.archive(record.id, reason="不再需要")

    assert (memory_root / "CORE.md").is_file()
    assert await manager.core.load() != ""


# ----------------------------------------------------------------------
# Memory CRUD
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_assigns_incrementing_ids(memory_root: Path) -> None:
    manager = await _manager(memory_root)

    first = await manager.create(title="A", summary="a", content="内容A")
    second = await manager.create(title="B", summary="b", content="内容B")

    assert first.id == "M001"
    assert second.id == "M002"
    assert (memory_root / "active" / "M001.md").is_file()
    assert (memory_root / "active" / "M002.md").is_file()


@pytest.mark.asyncio
async def test_concurrent_create_assigns_unique_ids(memory_root: Path) -> None:
    """Manager 必须串行化文件写入，不能让并发创建互相覆盖。"""

    import asyncio

    manager = await _manager(memory_root)
    records = await asyncio.gather(
        *(
            manager.create(
                title=f"记忆 {index}",
                summary=f"cue {index}",
                content=f"内容 {index}",
            )
            for index in range(10)
        )
    )

    assert {record.id for record in records} == {
        f"M{index:03d}" for index in range(1, 11)
    }
    assert await manager.store.count_active() == 10


@pytest.mark.asyncio
async def test_read_returns_record(memory_root: Path) -> None:
    manager = await _manager(memory_root)
    record = await manager.create(title="A", summary="a", content="内容A")

    loaded = await manager.read(record.id)

    assert loaded is not None
    assert loaded.title == "A"
    assert loaded.content == "内容A"


@pytest.mark.asyncio
async def test_read_missing_returns_none(memory_root: Path) -> None:
    manager = await _manager(memory_root)

    assert await manager.read("M999") is None


@pytest.mark.asyncio
async def test_memory_id_rejects_path_traversal(memory_root: Path) -> None:
    manager = await _manager(memory_root)

    with pytest.raises(ValueError, match="memory id"):
        await manager.read("../../CORE")


@pytest.mark.asyncio
async def test_create_rejects_content_larger_than_tool_read_budget(
    memory_root: Path,
) -> None:
    manager = await _manager(memory_root)

    with pytest.raises(ValueError, match="memory content exceeds"):
        await manager.create(title="过长", summary="过长正文", content="x" * 12_001)

    assert await manager.list() == ()


@pytest.mark.asyncio
async def test_update_changes_content(memory_root: Path) -> None:
    manager = await _manager(memory_root)
    record = await manager.create(title="A", summary="a", content="旧内容")

    updated = await manager.update(record.id, content="新内容", reason="修正事实")

    assert updated.content == "新内容"
    assert (await manager.read(record.id)).content == "新内容"


@pytest.mark.asyncio
async def test_update_missing_raises(memory_root: Path) -> None:
    manager = await _manager(memory_root)

    with pytest.raises(KeyError):
        await manager.update("M999", content="内容", reason="修正")


@pytest.mark.asyncio
async def test_archive_moves_to_archive_and_sets_status(memory_root: Path) -> None:
    manager = await _manager(memory_root)
    record = await manager.create(title="A", summary="a", content="内容A")

    archived = await manager.archive(record.id, reason="已经过时")

    assert archived.status is MemoryStatus.ARCHIVED
    assert not (memory_root / "active" / f"{record.id}.md").exists()
    assert (memory_root / "archive" / f"{record.id}.md").is_file()
    assert all(item.id != record.id for item in await manager.list())


@pytest.mark.asyncio
async def test_archived_memory_cannot_be_updated_or_return_to_active(
    memory_root: Path,
) -> None:
    manager = await _manager(memory_root)
    record = await manager.create(title="A", summary="a", content="原内容")
    await manager.archive(record.id, reason="已经过时")
    archived_path = memory_root / "archive" / f"{record.id}.md"
    before = archived_path.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="only active memory"):
        await manager.update(record.id, content="错误恢复", reason="不应成功")

    assert archived_path.read_text(encoding="utf-8") == before
    assert not (memory_root / "active" / f"{record.id}.md").exists()


@pytest.mark.asyncio
async def test_initialize_repairs_interrupted_archive(memory_root: Path) -> None:
    manager = await _manager(memory_root)
    record = await manager.create(title="A", summary="a", content="原内容")
    interrupted = MemoryRecord(
        **{
            **record.model_dump(),
            "status": MemoryStatus.ARCHIVED,
            "archive_reason": "模拟移动前中断",
        }
    )
    active_path = memory_root / "active" / f"{record.id}.md"
    active_path.write_text(interrupted.render_markdown(), encoding="utf-8")

    restarted = await _manager(memory_root)

    assert not active_path.exists()
    assert (memory_root / "archive" / f"{record.id}.md").is_file()
    assert await restarted.list() == ()
    assert f"[{record.id}]" not in (await restarted.index.load() or "")


@pytest.mark.asyncio
async def test_update_and_archive_reasons_are_persisted(memory_root: Path) -> None:
    manager = await _manager(memory_root)
    record = await manager.create(title="A", summary="a", content="旧内容")

    await manager.update(record.id, content="新内容", reason="用户修正了事实")
    archived = await manager.archive(record.id, reason="该背景已经失效")
    loaded = await manager.store.load(record.id)

    assert loaded is not None
    assert archived.last_update_reason == "用户修正了事实"
    assert loaded.last_update_reason == "用户修正了事实"
    assert loaded.archive_reason == "该背景已经失效"


@pytest.mark.asyncio
async def test_list_returns_only_active(memory_root: Path) -> None:
    manager = await _manager(memory_root)
    first = await manager.create(title="A", summary="a", content="内容A")
    await manager.create(title="B", summary="b", content="内容B")
    await manager.archive(first.id, reason="清理测试数据")

    ids = {record.id for record in await manager.list()}

    assert ids == {"M002"}


# ----------------------------------------------------------------------
# 运行时元数据
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_increments_access_count(memory_root: Path) -> None:
    manager = await _manager(memory_root)
    record = await manager.create(title="A", summary="a", content="内容A")

    await manager.read(record.id)
    await manager.read(record.id)

    loaded = await manager.read(record.id)
    assert loaded is not None
    assert loaded.access_count == 3


@pytest.mark.asyncio
async def test_read_updates_last_accessed_at(memory_root: Path) -> None:
    manager = await _manager(memory_root)
    record = await manager.create(title="A", summary="a", content="内容A")
    before = record.last_accessed_at

    loaded = await manager.read(record.id)

    assert loaded is not None
    assert loaded.last_accessed_at >= before


@pytest.mark.asyncio
async def test_update_refreshes_updated_at(memory_root: Path) -> None:
    manager = await _manager(memory_root)
    record = await manager.create(title="A", summary="a", content="旧内容")
    before = record.updated_at

    updated = await manager.update(record.id, content="新内容", reason="修正事实")

    assert updated.updated_at >= before


# ----------------------------------------------------------------------
# INDEX 投影
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_rebuilds_index(memory_root: Path) -> None:
    manager = await _manager(memory_root)
    await manager.create(title="第一", summary="cue-1", content="内容1")

    index_text = await manager.index.load()
    assert index_text is not None
    assert "[M001] 第一" in index_text
    assert "cue-1" in index_text


@pytest.mark.asyncio
async def test_initialize_repairs_stale_index_from_active_files(
    memory_root: Path,
) -> None:
    manager = await _manager(memory_root)
    await manager.create(title="第一", summary="cue-1", content="内容1")
    (memory_root / "INDEX.md").write_text("stale index", encoding="utf-8")

    restarted = await _manager(memory_root)
    index_text = await restarted.index.load()

    assert index_text is not None
    assert "[M001] 第一" in index_text
    assert "stale index" not in index_text


@pytest.mark.asyncio
async def test_archive_removes_from_index(memory_root: Path) -> None:
    manager = await _manager(memory_root)
    first = await manager.create(title="第一", summary="cue-1", content="内容1")
    await manager.create(title="第二", summary="cue-2", content="内容2")

    await manager.archive(first.id, reason="已经过时")

    index_text = await manager.index.load()
    assert index_text is not None
    assert "[M001]" not in index_text
    assert "[M002] 第二" in index_text


@pytest.mark.asyncio
async def test_index_contains_cue_not_full_content(memory_root: Path) -> None:
    manager = await _manager(memory_root)
    await manager.create(title="第一", summary="短提示", content="完整记忆正文内容A")

    index_text = await manager.index.load()
    assert index_text is not None
    assert "短提示" in index_text
    assert "完整记忆正文内容A" not in index_text


# ----------------------------------------------------------------------
# 容量维护
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_active_below_limit_does_not_require_maintenance(
    memory_root: Path,
) -> None:
    manager = await _manager(memory_root)
    for index in range(25):
        await manager.create(title=f"m{index}", summary=f"s{index}", content="c")

    assert await manager.maintenance_required() is False


@pytest.mark.asyncio
async def test_26th_memory_triggers_maintenance(memory_root: Path) -> None:
    manager = await _manager(memory_root)
    for index in range(26):
        await manager.create(title=f"m{index}", summary=f"s{index}", content="c")

    assert await manager.store.count_active() == 26
    assert await manager.maintenance_required() is True


@pytest.mark.asyncio
async def test_model_directed_maintenance_can_restore_capacity(
    memory_root: Path,
) -> None:
    """模型收到维护信号后执行 ARCHIVE，最终必须恢复到容量上限。"""

    manager = await _manager(memory_root)
    registry = ToolRegistry()
    register_memory_tools(registry, manager)
    register_memory_write_tools(registry, manager)
    for index in range(25):
        await manager.create(title=f"m{index}", summary=f"s{index}", content="c")

    result = await registry.get("memory.create").execute(
        {"title": "第 26 条", "summary": "触发维护", "content": "新内容"}
    )

    assert result["maintenance_required"] is True
    candidate = result["candidates"][0]["id"]
    await registry.get("memory.archive").execute(
        {"memory_id": candidate, "reason": "模型维护决定：不再保留"}
    )
    assert await manager.store.count_active() == 25
    assert await manager.maintenance_required() is False


@pytest.mark.asyncio
async def test_maintenance_selects_least_retained_candidates(
    memory_root: Path,
) -> None:
    now = datetime.now(UTC)
    manager = await _manager(memory_root)
    records = [
        MemoryRecord(
            id=f"M{index:03d}",
            title=f"t{index}",
            summary=f"s{index}",
            content="c",
            created_at=now,
            updated_at=now - timedelta(hours=(6 - index) * 10),
            last_accessed_at=now - timedelta(hours=(6 - index) * 20),
            access_count=index,
        )
        for index in range(1, 6)
    ]

    candidates = manager.maintenance.select_candidates(records, limit=3)

    # M001 最久未访问/更新且访问最少，应排在最前。
    assert [item.id for item in candidates] == ["M001", "M002", "M003"]


# ----------------------------------------------------------------------
# Runtime 注入
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_messages_include_core_index_policy(memory_root: Path) -> None:
    manager = await _manager(memory_root)
    await manager.core.update("用户身份：开发者")
    await manager.create(title="第一", summary="cue-1", content="完整正文")

    messages = await manager.context_messages()
    names = {message.name for message in messages}

    assert CORE_MEMORY_MESSAGE_NAME in names
    assert MEMORY_INDEX_MESSAGE_NAME in names
    assert MEMORY_POLICY_MESSAGE_NAME in names
    # 普通记忆完整正文不注入；只有 INDEX cue。
    assert not any(
        "完整正文" in (message.content or "") for message in messages
    )
    core_message = next(
        message for message in messages if message.name == CORE_MEMORY_MESSAGE_NAME
    )
    assert (core_message.content or "").count("# Core Memory") == 1


@pytest.mark.asyncio
async def test_policy_message_guides_model_directed_recall() -> None:
    assert "memory.read" in MEMORY_POLICY_PROMPT
    assert "after the run" in MEMORY_POLICY_PROMPT
    assert "core_memory.update" in MEMORY_POLICY_PROMPT
    assert "Task" in MEMORY_POLICY_PROMPT
    assert "Skills" in MEMORY_POLICY_PROMPT


def test_write_policy_rejects_transient_and_procedural_content() -> None:
    from app.memory import MEMORY_WRITE_POLICY

    # 当前任务临时状态（Task 领域）不应进入 Memory。
    assert "transient state of the current task" in MEMORY_WRITE_POLICY
    # 可复用流程属于 Skills，不应进入 Memory。
    assert "procedural knowledge that belongs to Skills" in MEMORY_WRITE_POLICY
    # 不确定时默认不创建。
    assert "When in doubt" in MEMORY_WRITE_POLICY
    assert "do not create a memory" in MEMORY_WRITE_POLICY
    # 用户明确长期偏好允许创建。
    assert "durable user requirement" in MEMORY_WRITE_POLICY


# ----------------------------------------------------------------------
# 语义工具
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_memory_tools_exposes_only_main_agent_tools(
    memory_root: Path,
) -> None:
    manager = await _manager(memory_root)
    registry = ToolRegistry()

    register_memory_tools(registry, manager)

    assert set(registry.names()) == {
        "memory.read",
        "memory.list",
        "core_memory.update",
        "core_memory.remove",
    }


@pytest.mark.asyncio
async def test_memory_create_and_read_tools_roundtrip(memory_root: Path) -> None:
    manager = await _manager(memory_root)
    registry = ToolRegistry()
    register_memory_tools(registry, manager)
    register_memory_write_tools(registry, manager)

    created = await registry.get("memory.create").execute(
        {
            "title": "用户偏好",
            "summary": "偏好中文",
            "content": "用户长期偏好使用中文交流。",
        }
    )
    assert created["id"] == "M001"

    read = await registry.get("memory.read").execute(
        {"memory_id": "M001"}
    )
    assert read["found"] is True
    assert "使用中文交流" in read["content"]
    # read 增加了访问次数。
    loaded = await manager.read("M001")
    assert loaded is not None
    assert loaded.access_count == 2


@pytest.mark.asyncio
async def test_memory_list_returns_cues_without_content(memory_root: Path) -> None:
    manager = await _manager(memory_root)
    registry = ToolRegistry()
    register_memory_tools(registry, manager)
    await manager.create(title="第一", summary="cue-1", content="机密正文")

    result = await registry.get("memory.list").execute({})

    assert result["memories"][0]["title"] == "第一"
    assert "机密正文" not in str(result)


@pytest.mark.asyncio
async def test_memory_update_and_archive_tools(memory_root: Path) -> None:
    manager = await _manager(memory_root)
    registry = ToolRegistry()
    register_memory_tools(registry, manager)
    register_memory_write_tools(registry, manager)
    record = await manager.create(title="第一", summary="cue-1", content="旧正文")

    updated = await registry.get("memory.update").execute(
        {"memory_id": record.id, "content": "新正文", "reason": "修正"}
    )
    assert updated["updated"] is True

    archived = await registry.get("memory.archive").execute(
        {"memory_id": record.id, "reason": "过时"}
    )
    assert archived["status"] == "archived"
    assert (await manager.read(record.id)) is None


@pytest.mark.asyncio
async def test_memory_tools_validate_required_arguments(memory_root: Path) -> None:
    manager = await _manager(memory_root)
    registry = ToolRegistry()
    register_memory_tools(registry, manager)
    register_memory_write_tools(registry, manager)

    with pytest.raises(ValueError, match="'memory_id'"):
        await registry.get("memory.read").execute({})
    with pytest.raises(ValueError, match="'content'"):
        await registry.get("memory.create").execute(
            {"title": "t", "summary": "s", "content": ""}
        )


@pytest.mark.asyncio
async def test_core_update_tool_requires_exact_current_user_statement(
    memory_root: Path,
) -> None:
    manager = await _manager(memory_root)
    registry = ToolRegistry()
    register_memory_tools(registry, manager)
    tool = registry.get("core_memory.update")
    arguments = {
        "key": "communication.language",
        "value": "始终使用中文交流。",
        "reason": "用户明确表达全局长期偏好",
        "explicit_user_statement": "以后都使用中文和我交流",
    }

    with pytest.raises(ValueError, match="copied exactly"):
        await tool.execute_with_context(
            arguments,
            ToolExecutionContext(
                tool_call=ToolCall(id="core-1", name="core_memory.update"),
                user_input="请帮我检查代码",
            ),
        )

    assert await manager.core.load() == ""


@pytest.mark.asyncio
async def test_core_update_tool_writes_through_harness(memory_root: Path) -> None:
    manager = await _manager(memory_root)
    registry = ToolRegistry()
    register_memory_tools(registry, manager)
    tool = registry.get("core_memory.update")
    statement = "以后都使用中文和我交流"

    result = await tool.execute_with_context(
        {
            "key": "communication.language",
            "value": "始终使用中文交流。",
            "reason": "用户明确表达全局长期偏好",
            "explicit_user_statement": statement,
        },
        ToolExecutionContext(
            tool_call=ToolCall(id="core-1", name="core_memory.update"),
            user_input=f"请记住，{statement}。",
        ),
    )

    assert result["created"] is True
    assert "始终使用中文交流" in await manager.core.load()


@pytest.mark.asyncio
async def test_core_remove_tool_requires_current_user_revocation(
    memory_root: Path,
) -> None:
    manager = await _manager(memory_root)
    await manager.upsert_core(
        key="communication.language",
        value="始终使用中文交流。",
        reason="用户明确表达全局长期偏好",
        source_statement="以后都使用中文和我交流",
    )
    registry = ToolRegistry()
    register_memory_tools(registry, manager)
    tool = registry.get("core_memory.remove")
    arguments = {
        "key": "communication.language",
        "reason": "用户撤销了长期语言偏好",
        "explicit_user_statement": "不用再记住语言偏好",
    }

    with pytest.raises(ValueError, match="copied exactly"):
        await tool.execute_with_context(
            arguments,
            ToolExecutionContext(
                tool_call=ToolCall(id="core-2", name="core_memory.remove"),
                user_input="继续检查代码",
            ),
        )

    result = await tool.execute_with_context(
        arguments,
        ToolExecutionContext(
            tool_call=ToolCall(id="core-3", name="core_memory.remove"),
            user_input="不用再记住语言偏好",
        ),
    )

    assert result == {"key": "communication.language", "removed": True}
    assert "始终使用中文交流" not in await manager.core.load()
