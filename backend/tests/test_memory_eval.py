"""Memory Eval 指标与跨 namespace 污染场景。"""

from __future__ import annotations

from app.memory import (
    HashMemoryEmbedder,
    HybridMemoryRetriever,
    MemoryDraft,
    MemorySource,
    MemoryStatus,
    MemoryType,
    MemoryWriteBudget,
    MemoryWriter,
    SQLiteMemoryStore,
)
from tests.eval.memory_metrics import evaluate_memory_retrieval


async def test_memory_eval_measures_recall_and_isolation(tmp_path) -> None:
    embedder = HashMemoryEmbedder(32)
    store = SQLiteMemoryStore(tmp_path / "memory.db", embedding_dimensions=32)
    await store.initialize()
    writer = MemoryWriter(
        store,
        embedder,
        budget=MemoryWriteBudget(per_run=100, per_session=100, per_day=100),
    )
    target = await writer.write(
        MemoryDraft(
            namespace="project:oneagent",
            memory_type=MemoryType.PROCEDURE,
            content="reasoning 模型生成短摘要时关闭 thinking",
            status=MemoryStatus.ACTIVE,
            source=MemorySource(session_id="eval", run_id="target"),
        )
    )
    for index in range(12):
        await writer.write(
            MemoryDraft(
                namespace=f"project:noise-{index}",
                memory_type=MemoryType.PROCEDURE,
                content="reasoning 模型生成短摘要时关闭 thinking",
                status=MemoryStatus.ACTIVE,
                source=MemorySource(session_id="eval", run_id=f"noise-{index}"),
            )
        )
    retriever = HybridMemoryRetriever(store, embedder)
    results = await retriever.retrieve(
        "reasoning 摘要 thinking",
        namespaces=("project:oneagent",),
        limit=3,
    )
    metrics = evaluate_memory_retrieval(
        results,
        expected_ids={target.memory.id},
        allowed_namespaces={"project:oneagent"},
    )

    assert metrics.recall_at_k == 1.0
    assert metrics.reciprocal_rank == 1.0
    assert metrics.namespace_violations == 0
    assert metrics.inactive_violations == 0
