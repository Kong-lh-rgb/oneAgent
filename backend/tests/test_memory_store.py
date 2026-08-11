"""Memory 生命周期与混合检索的离线测试。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.memory import (
    HashMemoryEmbedder,
    HybridMemoryRetriever,
    MemoryDraft,
    MemoryExtractor,
    MemoryManager,
    MemorySource,
    MemoryStatus,
    MemoryType,
    MemoryWriteAction,
    MemoryWriteBudget,
    MemoryWriter,
    SQLiteMemoryStore,
)


@pytest.fixture
async def memory_system(tmp_path):
    embedder = HashMemoryEmbedder(32)
    store = SQLiteMemoryStore(
        tmp_path / "oneagent.db",
        embedding_dimensions=embedder.dimensions,
    )
    await store.initialize()
    writer = MemoryWriter(store, embedder)
    retriever = HybridMemoryRetriever(store, embedder)
    return store, writer, retriever


def _draft(
    content: str,
    *,
    key: str | None = "project.database",
    status: MemoryStatus = MemoryStatus.ACTIVE,
    memory_type: MemoryType = MemoryType.FACT,
    namespace: str = "project:oneagent",
    run_id: str = "run-1",
) -> MemoryDraft:
    return MemoryDraft(
        namespace=namespace,
        memory_type=memory_type,
        key=key,
        content=content,
        status=status,
        importance=0.8,
        confidence=0.95,
        source=MemorySource(session_id="session-1", run_id=run_id),
    )


async def test_sqlite_fts5_and_vec_are_initialized(memory_system) -> None:
    store, _, _ = memory_system
    async with store._connect() as database:
        cursor = await database.execute("SELECT vec_version()")
        vec_version = (await cursor.fetchone())[0]
        tables = {
            row[0]
            for row in await (
                await database.execute(
                    "SELECT name FROM sqlite_master WHERE name IN "
                    "('memories','memory_fts','memory_vectors')"
                )
            ).fetchall()
        }

    assert vec_version == "v0.1.9"
    assert tables == {"memories", "memory_fts", "memory_vectors"}


async def test_fingerprint_deduplicates_punctuation(memory_system) -> None:
    _, writer, _ = memory_system
    first = await writer.write(_draft("项目决定使用 SQLite。"))
    duplicate = await writer.write(_draft("项目决定 使用 SQLite!"))

    assert first.action is MemoryWriteAction.CREATED
    assert duplicate.action is MemoryWriteAction.DUPLICATE
    assert duplicate.memory == first.memory


async def test_active_fact_conflict_preserves_history(memory_system) -> None:
    store, writer, _ = memory_system
    first = await writer.write(_draft("默认数据库是 SQLite"))
    second = await writer.write(_draft("默认数据库改为 PostgreSQL"))

    assert second.action is MemoryWriteAction.SUPERSEDED
    assert second.previous is not None
    assert second.previous.status is MemoryStatus.SUPERSEDED
    assert second.memory is not None
    assert second.memory.supersedes_id == first.memory.id
    history = await store.list(
        statuses=(MemoryStatus.ACTIVE, MemoryStatus.SUPERSEDED)
    )
    assert len(history) == 2


async def test_candidate_requires_promotion_before_retrieval(memory_system) -> None:
    store, writer, retriever = memory_system
    result = await writer.write(
        _draft(
            "DeepSeek 摘要曾因 thinking 消耗预算而为空",
            key=None,
            status=MemoryStatus.CANDIDATE,
            memory_type=MemoryType.EPISODE,
        )
    )

    assert await retriever.retrieve(
        "DeepSeek 摘要为空",
        namespaces=("project:oneagent",),
    ) == ()
    promoted = await writer.promote(result.memory.id, expected_revision=1)
    recalled = await retriever.retrieve(
        "DeepSeek 摘要为空",
        namespaces=("project:oneagent",),
    )
    assert promoted.status is MemoryStatus.ACTIVE
    assert recalled[0].memory.id == promoted.id
    assert (await store.get(promoted.id)).confirmation_count == 1


async def test_candidate_can_be_promoted_by_real_use(memory_system) -> None:
    store, writer, _ = memory_system
    result = await writer.write(
        _draft(
            "处理摘要空输出时先检查 thinking 配置",
            key=None,
            status=MemoryStatus.CANDIDATE,
            memory_type=MemoryType.PROCEDURE,
        )
    )

    promoted = await writer.promote_after_use(
        result.memory.id,
        expected_revision=1,
    )

    stored = await store.get(promoted.id)
    assert stored.status is MemoryStatus.ACTIVE
    assert stored.use_count == 1
    assert stored.confirmation_count == 0


async def test_hybrid_retrieval_exposes_both_rank_sources(memory_system) -> None:
    _, writer, retriever = memory_system
    await writer.write(
        _draft(
            "reasoning model 生成短摘要时应关闭 thinking",
            key=None,
            memory_type=MemoryType.PROCEDURE,
        )
    )
    await writer.write(
        _draft(
            "OneAgent 使用 SQLite 保存本地状态",
            run_id="run-2",
        )
    )

    results = await retriever.retrieve(
        "reasoning model thinking",
        namespaces=("project:oneagent",),
    )

    assert results[0].memory.memory_type is MemoryType.PROCEDURE
    assert results[0].lexical_rank == 1
    assert results[0].vector_rank is not None


async def test_namespace_filter_prevents_cross_project_recall(memory_system) -> None:
    _, writer, retriever = memory_system
    await writer.write(_draft("OneAgent 使用 SQLite"))
    await writer.write(
        _draft(
            "另一个项目使用 SQLite",
            namespace="project:other",
            run_id="run-2",
        )
    )

    results = await retriever.retrieve(
        "SQLite",
        namespaces=("project:oneagent",),
    )

    assert results
    assert {result.memory.namespace for result in results} == {"project:oneagent"}


async def test_write_budget_rejects_excess_without_writing(tmp_path) -> None:
    embedder = HashMemoryEmbedder(16)
    store = SQLiteMemoryStore(tmp_path / "memory.db", embedding_dimensions=16)
    await store.initialize()
    writer = MemoryWriter(
        store,
        embedder,
        budget=MemoryWriteBudget(per_run=1, per_session=10, per_day=10),
    )
    await writer.write(_draft("第一条事实", key="fact.one"))
    rejected = await writer.write(_draft("第二条事实", key="fact.two"))

    assert rejected.action is MemoryWriteAction.REJECTED
    assert "run" in rejected.reason
    assert len(await store.list()) == 1


async def test_archived_memory_leaves_search_results(memory_system) -> None:
    store, writer, retriever = memory_system
    result = await writer.write(_draft("项目数据库使用 SQLite"))
    archived = await store.change_status(
        result.memory.id,
        MemoryStatus.ARCHIVED,
        expected_revision=1,
    )

    assert archived.status is MemoryStatus.ARCHIVED
    assert await retriever.retrieve(
        "SQLite",
        namespaces=("project:oneagent",),
    ) == ()
    assert await store.get(archived.id) == archived


class _FakeExtractor(MemoryExtractor):
    async def extract(self, observation, *, namespace, source):
        return (
            MemoryDraft(
                namespace=namespace,
                memory_type=MemoryType.FACT,
                key="response.language",
                content="用户要求以后使用中文",
                status=MemoryStatus.ACTIVE,
                source=source,
            ),
        )


async def test_manager_observe_and_context_message(memory_system) -> None:
    _, writer, retriever = memory_system
    manager = MemoryManager(
        writer=writer,
        retriever=retriever,
        extractor=_FakeExtractor(),
    )
    ignored = await manager.observe(
        "你好",
        namespace="user:local",
        source=MemorySource(session_id="session-1", run_id="run-1"),
    )
    written = await manager.observe(
        "请记住，以后回答必须优先使用中文。",
        namespace="user:local",
        source=MemorySource(session_id="session-1", run_id="run-1"),
        explicit_user_text="请记住，以后回答必须优先使用中文。",
    )
    message = await manager.context_message(
        "以后使用什么语言回答？",
        namespaces=("user:local",),
    )

    assert ignored == ()
    assert written[0].action is MemoryWriteAction.CREATED
    assert message is not None
    assert "用户要求以后使用中文" in message.content


async def test_model_inference_cannot_self_promote_to_active(memory_system) -> None:
    store, writer, retriever = memory_system
    manager = MemoryManager(
        writer=writer,
        retriever=retriever,
        extractor=_FakeExtractor(),
    )

    written = await manager.observe(
        "这个任务失败了，模型推测用户偏好中文回答。",
        namespace="user:local",
        source=MemorySource(session_id="session-1", run_id="run-2"),
        explicit_user_text="这个任务失败了，请分析原因。",
    )

    assert written[0].memory.status is MemoryStatus.CANDIDATE
    assert await store.list(statuses=(MemoryStatus.ACTIVE,)) == ()


async def test_access_telemetry_does_not_advance_revision(memory_system) -> None:
    store, writer, retriever = memory_system
    result = await writer.write(_draft("项目数据库使用 SQLite"))
    await retriever.retrieve("SQLite", namespaces=("project:oneagent",))
    accessed = await store.get(result.memory.id)

    assert accessed.access_count == 1
    assert accessed.revision == 1


def test_source_message_requires_session() -> None:
    with pytest.raises(ValueError, match="requires session_id"):
        MemorySource(message_id="message-1")


def test_memory_timestamps_are_utc() -> None:
    assert datetime.now(UTC).utcoffset().total_seconds() == 0


async def test_resolve_prefix_filters_namespace_before_uniqueness(
    tmp_path,
    monkeypatch,
) -> None:
    identifiers = iter(
        (
            "abcde000000000000000000000000000",
            "abcde111111111111111111111111111",
        )
    )

    class _Identifier:
        def __init__(self, value: str) -> None:
            self.hex = value

    monkeypatch.setattr(
        "app.memory.store.uuid4",
        lambda: _Identifier(next(identifiers)),
    )
    embedder = HashMemoryEmbedder(16)
    store = SQLiteMemoryStore(tmp_path / "memory.db", embedding_dimensions=16)
    await store.initialize()
    writer = MemoryWriter(store, embedder)
    first = await writer.write(_draft("项目一使用 SQLite"))
    second = await writer.write(
        _draft(
            "项目二使用 SQLite",
            namespace="project:other",
            run_id="run-2",
        )
    )

    assert await store.resolve(
        "abcde",
        namespaces=("project:oneagent",),
    ) == first.memory
    assert await store.resolve(
        "abcde",
        namespaces=("project:other",),
    ) == second.memory
    with pytest.raises(ValueError, match="前缀不唯一"):
        await store.resolve(
            "abcde",
            namespaces=("project:oneagent", "project:other"),
        )


async def test_confirm_and_archive_enforce_lifecycle(memory_system) -> None:
    store, writer, _ = memory_system
    candidate = await writer.write(
        _draft(
            "候选操作经验",
            key=None,
            status=MemoryStatus.CANDIDATE,
            memory_type=MemoryType.PROCEDURE,
        )
    )
    confirmed = await writer.promote(candidate.memory.id, expected_revision=1)

    with pytest.raises(ValueError, match="only candidate"):
        await writer.promote(confirmed.id, expected_revision=2)
    archived = await writer.archive(confirmed.id, expected_revision=2)
    assert archived.status is MemoryStatus.ARCHIVED
    with pytest.raises(ValueError, match="only candidate or active"):
        await writer.archive(archived.id, expected_revision=3)
    assert await store.get(archived.id) == archived
