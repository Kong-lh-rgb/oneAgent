"""Run Manager V1 生命周期测试。

覆盖：
1. start → RUNNING → COMPLETED
2. Runtime 异常 → FAILED 且 error 被记录
3. cancel 正在执行的 Run → 不再产生新的 Agent Step → CANCELLED
4. 进程重启 reconciliation：RUNNING + 可恢复 Checkpoint → INTERRUPTED
5. recover INTERRUPTED Run → 使用 Checkpoint → 继续执行 → COMPLETED
6. 已完成 Tool Result 不重复执行
7. 无效状态转换被拒绝
8. completed Run 不能 cancel
9. 一个 Conversation 下可以存在多个 Run
10. RunManager 不影响 Trace / Checkpoint 原有行为

全部使用 fake model / fake tool，不调用真实模型 API。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest
from pydantic import SecretStr

from app.agent.events import AgentEventType
from app.agent.result import AgentStopReason
from app.agent.runtime import AgentRuntime
from app.checkpoint import (
    CHECKPOINT_CONTEXT_MESSAGE_NAME,
    CheckpointPhase,
    CheckpointStatus,
    SQLiteCheckpointStore,
)
from app.models.adapter import ModelAdapter
from app.models.config import ModelSettings, ProviderConfig
from app.models.registry import ModelAdapterRegistry
from app.models.types import (
    ApiStyle,
    Message,
    MessageRole,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    ToolCall,
    ToolDefinition,
)
from app.run import RunManager, RunStatus, SQLiteRunStore
from app.tools.base import BaseTool
from app.tools.registry import ToolRegistry

# ---------------------------------------------------------------------------
# Fake helpers（与 test_agent_runtime 同构，避免真实 API）
# ---------------------------------------------------------------------------


class FakeModelAdapter(ModelAdapter):
    def __init__(
        self,
        config: ProviderConfig,
        responses: Sequence[ModelResponse | Exception],
    ) -> None:
        super().__init__(config)
        self.responses = list(responses)
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def close(self) -> None:
        pass


class BlockingModelAdapter(ModelAdapter):
    """阻塞在模型请求上，直到被取消（用于 cancel 测试）。"""

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self.started = asyncio.Event()
        self.cancelled = False
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        raise AssertionError("阻塞模型不应正常完成")

    async def close(self) -> None:
        pass


class CountingTool(BaseTool):
    definition = ToolDefinition(
        name="count",
        description="Count executions",
        parameters={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
        },
    )

    def __init__(self) -> None:
        self.executions = 0

    async def execute(self, arguments: dict[str, object]) -> str:
        self.executions += 1
        return str(arguments["value"])


class BlockingTool(BaseTool):
    definition = ToolDefinition(
        name="blocking_tool",
        description="Wait until cancelled",
        parameters={"type": "object", "properties": {}},
    )

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = False

    async def execute(self, arguments: dict[str, object]) -> str:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        raise AssertionError("阻塞工具不应正常完成")


def model_response(
    *,
    content: str | None = None,
    tool_calls: tuple[ToolCall, ...] = (),
    usage: ModelUsage | None = None,
) -> ModelResponse:
    return ModelResponse(
        id="fake-response",
        provider="fake",
        model="fake-model",
        message=Message(
            role=MessageRole.ASSISTANT,
            content=content,
            tool_calls=tool_calls,
        ),
        usage=usage or ModelUsage(),
    )


def fake_registry(
    responses: Sequence[ModelResponse | Exception],
) -> tuple[ModelAdapterRegistry, FakeModelAdapter]:
    config = ProviderConfig(
        provider="fake",
        model="fake-model",
        api_key=SecretStr("offline-test-key"),
        api_style=ApiStyle.CHAT_COMPLETIONS,
    )
    adapter = FakeModelAdapter(config, responses)
    registry = ModelAdapterRegistry(ModelSettings(_env_file=None))
    registry.register("fake", lambda _: adapter, config=config)
    return registry, adapter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def manager_factory(tmp_path):
    """构建一个 RunManager + 底层 store，供每个测试独立使用。"""

    async def build_manager(
        registry: ModelAdapterRegistry,
        tools: ToolRegistry | None = None,
        *,
        checkpoint_store: SQLiteCheckpointStore | None = None,
        run_store: SQLiteRunStore | None = None,
        provider: str = "fake",
        run_finalizers=(),
    ) -> tuple[RunManager, SQLiteRunStore, SQLiteCheckpointStore]:
        database = tmp_path / "vesta.db"
        run_store = run_store or SQLiteRunStore(database)
        checkpoint_store = checkpoint_store or SQLiteCheckpointStore(database)
        await run_store.initialize()
        await checkpoint_store.initialize()
        runtime = AgentRuntime(
            registry,
            tools or ToolRegistry(),
            provider=provider,
            checkpoint_store=checkpoint_store,
        )
        manager = RunManager(
            run_store, checkpoint_store, runtime, run_finalizers=run_finalizers
        )
        return manager, run_store, checkpoint_store

    return build_manager


# ---------------------------------------------------------------------------
# 1. start → RUNNING → COMPLETED
# ---------------------------------------------------------------------------


async def test_start_running_to_completed(manager_factory) -> None:
    build_manager = manager_factory
    registry, _ = fake_registry([model_response(content="完成")])
    manager, run_store, _ = await build_manager(registry)

    run_id, task = await manager.start("你好", conversation_id="conv-1")
    # start() 之后立即是 RUNNING（PENDING 是瞬时状态）。
    running = await manager.get_run(run_id)
    assert running is not None and running.status is RunStatus.RUNNING

    run = await manager.wait(run_id)
    assert run.status is RunStatus.COMPLETED
    assert run.stop_reason == AgentStopReason.FINAL_ANSWER.value
    assert run.completed_at is not None
    assert run.started_at is not None
    assert run.conversation_id == "conv-1"

    # 结果可读（CLI 依赖）
    result = manager.result(run_id)
    assert result is not None and result.ok is True


# ---------------------------------------------------------------------------
# 2. Runtime 异常 → FAILED 且 error 被记录
# ---------------------------------------------------------------------------


async def test_runtime_exception_marks_failed(manager_factory) -> None:
    build_manager = manager_factory
    registry, _ = fake_registry([RuntimeError("offline")])
    manager, _, _ = await build_manager(registry)

    run_id, _ = await manager.start("你好", conversation_id="conv-1")
    run = await manager.wait(run_id)

    assert run.status is RunStatus.FAILED
    assert run.error is not None and "offline" in run.error
    assert run.stop_reason == AgentStopReason.MODEL_ERROR.value


async def test_run_finalizer_runs_for_completed_and_failed(manager_factory) -> None:
    finalized: list[str] = []
    registry, _ = fake_registry([model_response(content="完成")])
    manager, _, _ = await manager_factory(registry, run_finalizers=(finalized.append,))
    completed_id, _ = await manager.start("完成")
    await manager.wait(completed_id)

    failed_registry, _ = fake_registry([RuntimeError("offline")])
    failed_manager, _, _ = await manager_factory(
        failed_registry, run_finalizers=(finalized.append,)
    )
    failed_id, _ = await failed_manager.start("失败")
    await failed_manager.wait(failed_id)
    assert finalized == [completed_id, failed_id]


async def test_run_finalizer_failure_does_not_change_terminal_state(
    manager_factory,
) -> None:
    def broken(_run_id: str) -> None:
        raise RuntimeError("cleanup failed")

    registry, _ = fake_registry([model_response(content="完成")])
    manager, _, _ = await manager_factory(registry, run_finalizers=(broken,))
    run_id, _ = await manager.start("完成")
    assert (await manager.wait(run_id)).status is RunStatus.COMPLETED


# ---------------------------------------------------------------------------
# 3. cancel 正在执行的 Run → CANCELLED，不再产生新的 Agent Step
# ---------------------------------------------------------------------------


async def test_cancel_running_run_during_model_request(
    manager_factory,
    tmp_path,
) -> None:
    build_manager = manager_factory
    config = ProviderConfig(
        provider="blocking",
        model="blocking-model",
        api_key=SecretStr("offline-test-key"),
        api_style=ApiStyle.CHAT_COMPLETIONS,
    )
    adapter = BlockingModelAdapter(config)
    registry = ModelAdapterRegistry(ModelSettings(_env_file=None))
    registry.register("blocking", lambda _: adapter, config=config)

    # runtime 必须能解析到 blocking provider，否则不会进入 complete()。
    finalized: list[str] = []
    manager, _, checkpoint_store = await build_manager(
        registry, provider="blocking", run_finalizers=(finalized.append,)
    )

    run_id, task = await manager.start("你好", conversation_id="conv-1")
    await adapter.started.wait()

    cancelled_run = await manager.cancel(run_id)

    assert cancelled_run.status is RunStatus.CANCELLED
    assert cancelled_run.completed_at is not None
    assert cancelled_run.error is not None
    # 只发起了一次模型请求（取消发生在第一个 Agent Step，不再有下一个 Step）。
    assert len(adapter.requests) == 1
    assert adapter.cancelled is True

    # Checkpoint 保持可恢复边界（未决/已完成工具不丢失）。
    checkpoint = await checkpoint_store.get(run_id)
    assert checkpoint is not None
    assert checkpoint.status is CheckpointStatus.INTERRUPTED
    assert checkpoint.phase is CheckpointPhase.MODEL_REQUEST
    assert checkpoint.step == 1
    assert finalized == [run_id]


async def test_cancel_running_run_during_tool_execution(manager_factory) -> None:
    build_manager = manager_factory
    call = ToolCall(id="uncertain-tool", name="blocking_tool", arguments={})
    registry, _ = fake_registry([model_response(tool_calls=(call,))])
    tool = BlockingTool()
    tools = ToolRegistry()
    tools.register(tool)

    manager, _, checkpoint_store = await build_manager(registry, tools)

    run_id, _ = await manager.start("执行阻塞工具", conversation_id="conv-1")
    await tool.started.wait()

    cancelled_run = await manager.cancel(run_id)

    assert cancelled_run.status is RunStatus.CANCELLED
    assert tool.cancelled is True
    checkpoint = await checkpoint_store.get(run_id)
    assert checkpoint is not None
    assert checkpoint.status is CheckpointStatus.INTERRUPTED
    assert checkpoint.phase is CheckpointPhase.TOOL_EXECUTION
    assert checkpoint.pending_tool_calls == (call,)
    assert checkpoint.completed_tool_results == ()


async def test_interrupt_running_run_preserves_checkpoint(manager_factory) -> None:
    build_manager = manager_factory
    config = ProviderConfig(
        provider="blocking",
        model="blocking-model",
        api_key=SecretStr("offline-test-key"),
        api_style=ApiStyle.CHAT_COMPLETIONS,
    )
    adapter = BlockingModelAdapter(config)
    registry = ModelAdapterRegistry(ModelSettings(_env_file=None))
    registry.register("blocking", lambda _: adapter, config=config)

    manager, _, checkpoint_store = await build_manager(
        registry, provider="blocking"
    )

    run_id, _ = await manager.start("你好", conversation_id="conv-1")
    await adapter.started.wait()

    interrupted = await manager.interrupt(run_id)

    assert interrupted.status is RunStatus.INTERRUPTED
    assert interrupted.completed_at is not None
    assert adapter.cancelled is True
    # Checkpoint 保留中断边界（可恢复）。
    checkpoint = await checkpoint_store.get(run_id)
    assert checkpoint is not None
    assert checkpoint.status is CheckpointStatus.INTERRUPTED
    # 幂等：再次 interrupt 返回当前状态，不报错。
    again = await manager.interrupt(run_id)
    assert again.status is RunStatus.INTERRUPTED


async def test_terminal_run_cancel_is_idempotent(manager_factory) -> None:
    build_manager = manager_factory
    registry, _ = fake_registry([model_response(content="完成")])
    manager, _, _ = await build_manager(registry)

    run_id, _ = await manager.start("你好", conversation_id="conv-1")
    run = await manager.wait(run_id)
    assert run.status is RunStatus.COMPLETED

    # 已终态 Run 的 cancel 是幂等 no-op，不再抛错（前端可能重复点击暂停）。
    again = await manager.cancel(run_id)
    assert again.status is RunStatus.COMPLETED


# ---------------------------------------------------------------------------
# 4. 进程重启 reconciliation
# ---------------------------------------------------------------------------


async def _make_stale_running_run(
    tmp_path,
    *,
    with_checkpoint: bool,
    checkpoint_status: CheckpointStatus = CheckpointStatus.RUNNING,
    user_message: str = "写入 output.md",
) -> tuple[str, SQLiteRunStore, SQLiteCheckpointStore, str]:
    """模拟进程崩溃前留下的持久化状态：RUNNING Run +（可选）Checkpoint。"""

    database = tmp_path / "vesta.db"
    run_store = SQLiteRunStore(database)
    checkpoint_store = SQLiteCheckpointStore(database)
    await run_store.initialize()
    await checkpoint_store.initialize()

    run = await run_store.create(
        conversation_id="conv-1",
        user_message=user_message,
    )
    await run_store.mark_started(run.id)
    if with_checkpoint:
        await checkpoint_store.start(
            run.id,
            conversation_id="conv-1",
            user_message=Message(role=MessageRole.USER, content=user_message),
        )
        await checkpoint_store.before_model(run.id, step=2)
        if checkpoint_status is CheckpointStatus.INTERRUPTED:
            await checkpoint_store.interrupt(run.id, error="process stopped")
    return run.id, run_store, checkpoint_store, str(database)


async def test_reconcile_running_with_recoverable_checkpoint_becomes_interrupted(
    tmp_path,
    manager_factory,
) -> None:
    build_manager = manager_factory
    run_id, _, _, database = await _make_stale_running_run(
        tmp_path,
        with_checkpoint=True,
        checkpoint_status=CheckpointStatus.RUNNING,
    )

    # 新进程：用同一数据库重新构造 manager，initialize 触发 reconciliation。
    registry, _ = fake_registry([model_response(content="完成")])
    run_store = SQLiteRunStore(database)
    checkpoint_store = SQLiteCheckpointStore(database)
    manager, _, _ = await build_manager(
        registry,
        run_store=run_store,
        checkpoint_store=checkpoint_store,
    )
    reconciled = await manager.initialize()

    assert [item.id for item in reconciled] == [run_id]
    assert reconciled[0].status is RunStatus.INTERRUPTED
    # Checkpoint 也同步转 INTERRUPTED，保持"可恢复"语义一致。
    checkpoint = await checkpoint_store.get(run_id)
    assert checkpoint is not None
    assert checkpoint.status is CheckpointStatus.INTERRUPTED
    # 不再显示 RUNNING（进程级事实）。
    still_running = await run_store.list_runs(status=RunStatus.RUNNING)
    assert still_running == ()


async def test_reconcile_running_without_checkpoint_becomes_failed(
    tmp_path,
    manager_factory,
) -> None:
    build_manager = manager_factory
    run_id, _, _, database = await _make_stale_running_run(
        tmp_path,
        with_checkpoint=False,
    )

    registry, _ = fake_registry([model_response(content="完成")])
    run_store = SQLiteRunStore(database)
    checkpoint_store = SQLiteCheckpointStore(database)
    manager, _, _ = await build_manager(
        registry,
        run_store=run_store,
        checkpoint_store=checkpoint_store,
    )
    reconciled = await manager.initialize()

    assert [item.id for item in reconciled] == [run_id]
    assert reconciled[0].status is RunStatus.FAILED
    assert "no recoverable checkpoint" in (reconciled[0].error or "")


async def test_reconcile_stale_pending_becomes_failed(
    tmp_path,
    manager_factory,
) -> None:
    """进程重启：遗留 PENDING Run（创建后从未开始）→ FAILED 终态，不留卡住。

    PENDING 不是终态；进程崩溃后它不可能再被启动，也没有 Checkpoint 可恢复，
    统一归入 FAILED，避免永久卡在 PENDING。
    """

    build_manager = manager_factory
    database = tmp_path / "vesta.db"
    run_store = SQLiteRunStore(database)
    await run_store.initialize()
    pending = await run_store.create(
        conversation_id="conv-1",
        user_message="从未开始的运行",
    )
    # 模拟进程在 create 之后、mark_started 之前崩溃：Run 停在 PENDING。
    assert pending.status is RunStatus.PENDING

    registry, _ = fake_registry([model_response(content="完成")])
    checkpoint_store = SQLiteCheckpointStore(database)
    await checkpoint_store.initialize()
    manager, _, _ = await build_manager(
        registry,
        run_store=run_store,
        checkpoint_store=checkpoint_store,
    )

    reconciled = await manager.initialize()

    assert [item.id for item in reconciled] == [pending.id]
    assert reconciled[0].status is RunStatus.FAILED
    assert "never started" in (reconciled[0].error or "")
    # 不再有 PENDING Run 残留。
    still_pending = await run_store.list_runs(status=RunStatus.PENDING)
    assert still_pending == ()
    # 已转终态，不能再被 recover。
    with pytest.raises(ValueError):
        await manager.recover(pending.id)


# ---------------------------------------------------------------------------
# 5. recover INTERRUPTED Run → 使用 Checkpoint → 继续执行 → COMPLETED
# ---------------------------------------------------------------------------


async def test_recover_interrupted_run_uses_checkpoint_and_completes(
    tmp_path,
    manager_factory,
) -> None:
    build_manager = manager_factory
    # 模拟一个中断的 Run：已有已完成工具结果 + 一个未决工具调用。
    database = tmp_path / "vesta.db"
    run_store = SQLiteRunStore(database)
    checkpoint_store = SQLiteCheckpointStore(database)
    await run_store.initialize()
    await checkpoint_store.initialize()

    done = ToolCall(id="done-1", name="count", arguments={"value": 1})
    pending = ToolCall(id="pending-1", name="count", arguments={"value": 2})
    old_run = await run_store.create(
        conversation_id="conv-1",
        user_message="执行任务",
    )
    await run_store.mark_started(old_run.id)
    await checkpoint_store.start(
        old_run.id,
        conversation_id="conv-1",
        user_message=Message(role=MessageRole.USER, content="执行任务"),
    )
    await checkpoint_store.before_model(old_run.id, step=1)
    await checkpoint_store.before_tools(
        old_run.id,
        step=1,
        tool_calls=(done, pending),
    )
    await checkpoint_store.complete_tool(
        old_run.id,
        result=_tool_result(done),
    )
    await checkpoint_store.interrupt(old_run.id, error="process stopped")

    # 新进程 reconciliation → 旧 Run INTERRUPTED。
    registry, adapter = fake_registry([model_response(content="已核对并继续")])
    manager, _, _ = await build_manager(
        registry,
        run_store=run_store,
        checkpoint_store=checkpoint_store,
    )
    await manager.initialize()
    old = await manager.get_run(old_run.id)
    assert old is not None and old.status is RunStatus.INTERRUPTED

    # recover：同一会话新 Run，复用旧 Checkpoint。
    new_run_id, _ = await manager.recover(
        old_run.id,
        history=(),
    )
    new_run = await manager.wait(new_run_id)

    assert new_run.status is RunStatus.COMPLETED
    assert new_run.recovered_from_run_id == old_run.id
    assert new_run.conversation_id == "conv-1"

    # 恢复证据被注入模型请求（使用 Checkpoint 的 completed_tool_results）。
    injected = next(
        message
        for message in adapter.requests[0].messages
        if message.name == CHECKPOINT_CONTEXT_MESSAGE_NAME
    )
    assert "done-1" in (injected.content or "")
    assert "pending-1" in (injected.content or "")

    # 旧 Checkpoint 被标记为已恢复，不再出现在未恢复查询里。
    assert await checkpoint_store.get_unrecovered(old_run.id) is None


def _tool_result(call: ToolCall):
    from app.models.types import ToolResult

    return ToolResult(
        tool_call_id=call.id,
        tool_name=call.name,
        success=True,
        output="ok",
        duration_ms=1.0,
    )


# ---------------------------------------------------------------------------
# 6. 已完成 Tool Result 不重复执行
# ---------------------------------------------------------------------------


async def test_completed_tool_results_are_not_reexecuted(tmp_path) -> None:
    database = tmp_path / "vesta.db"
    run_store = SQLiteRunStore(database)
    checkpoint_store = SQLiteCheckpointStore(database)
    await run_store.initialize()
    await checkpoint_store.initialize()

    done = ToolCall(id="done-1", name="count", arguments={"value": 1})
    old_run = await run_store.create(
        conversation_id="conv-1",
        user_message="统计",
    )
    await run_store.mark_started(old_run.id)
    await checkpoint_store.start(
        old_run.id,
        conversation_id="conv-1",
        user_message=Message(role=MessageRole.USER, content="统计"),
    )
    await checkpoint_store.before_model(old_run.id, step=1)
    await checkpoint_store.before_tools(old_run.id, step=1, tool_calls=(done,))
    await checkpoint_store.complete_tool(old_run.id, result=_tool_result(done))
    await checkpoint_store.interrupt(old_run.id, error="process stopped")

    tool = CountingTool()
    tools = ToolRegistry()
    tools.register(tool)
    # 恢复后模型直接给出最终答案（不再调用 count）→ 已完成工具不重复执行。
    registry, _ = fake_registry([model_response(content="统计完成")])
    runtime = AgentRuntime(
        registry,
        tools,
        provider="fake",
        checkpoint_store=checkpoint_store,
    )
    manager = RunManager(run_store, checkpoint_store, runtime)
    await manager.initialize()

    new_run_id, _ = await manager.recover(old_run.id, history=())
    new_run = await manager.wait(new_run_id)

    assert new_run.status is RunStatus.COMPLETED
    assert tool.executions == 0  # 已完成工具结果没有被重新执行
    checkpoint = await checkpoint_store.get(old_run.id)
    assert checkpoint is not None and checkpoint.recovered_by_run_id == new_run_id


# ---------------------------------------------------------------------------
# 7. 无效状态转换被拒绝
# ---------------------------------------------------------------------------


async def test_invalid_state_transitions_rejected(tmp_path) -> None:
    store = SQLiteRunStore(tmp_path / "vesta.db")
    await store.initialize()

    run = await store.create(conversation_id="conv-1", user_message="x")
    # PENDING → COMPLETED 非法（必须先 RUNNING）。
    with pytest.raises(ValueError, match="invalid run transition"):
        await store.mark_completed(run.id)
    # PENDING → FAILED 只允许出现在进程重启 reconciliation（stale PENDING
    # 归入 FAILED 终态），见 test_reconcile_stale_pending_becomes_failed。
    # 这里 PENDING → CANCELLED 仍非法。
    with pytest.raises(ValueError, match="invalid run transition"):
        await store.mark_cancelled(run.id)

    await store.mark_started(run.id)
    await store.mark_completed(run.id)
    # 终态不可再转换。
    with pytest.raises(ValueError, match="invalid run transition"):
        await store.mark_started(run.id)
    with pytest.raises(ValueError, match="invalid run transition"):
        await store.mark_cancelled(run.id)
    with pytest.raises(ValueError, match="invalid run transition"):
        await store.mark_interrupted(run.id)

    # INTERRUPTED 也是终态：中断后该 attempt 结束，不可再回到 RUNNING
    # （恢复 = recover() 创建新的 execution attempt，而不是把旧 Run 复活）。
    run2 = await store.create(conversation_id="conv-1", user_message="x")
    await store.mark_started(run2.id)
    await store.mark_interrupted(run2.id)
    assert run2 is not None
    with pytest.raises(ValueError, match="invalid run transition"):
        await store.mark_started(run2.id)
    with pytest.raises(ValueError, match="invalid run transition"):
        await store.mark_completed(run2.id)
    with pytest.raises(ValueError, match="invalid run transition"):
        await store.mark_cancelled(run2.id)

    # 终态 Run 在 Manager 层的 cancel 是幂等 no-op（不抛错）。
    manager_runtime_registry, _ = fake_registry([model_response(content="完成")])
    checkpoint_store = SQLiteCheckpointStore(tmp_path / "vesta.db")
    await checkpoint_store.initialize()
    manager = RunManager(
        store,
        checkpoint_store,
        AgentRuntime(
            manager_runtime_registry,
            ToolRegistry(),
            provider="fake",
            checkpoint_store=checkpoint_store,
        ),
    )
    again = await manager.cancel(run.id)
    assert again.status is RunStatus.COMPLETED


# ---------------------------------------------------------------------------
# 8. 一个 Conversation 下可以存在多个 Run
# ---------------------------------------------------------------------------


async def test_multiple_runs_in_one_conversation(manager_factory) -> None:
    build_manager = manager_factory
    registry, _ = fake_registry(
        [
            model_response(content="第一次"),
            model_response(content="第二次"),
        ]
    )
    manager, run_store, _ = await build_manager(registry)

    first_id, _ = await manager.start("问题一", conversation_id="conv-1")
    second_id, _ = await manager.start("问题二", conversation_id="conv-1")
    await manager.wait(first_id)
    await manager.wait(second_id)

    runs = await manager.list_runs(conversation_id="conv-1")
    assert len(runs) == 2
    assert {item.id for item in runs} == {first_id, second_id}
    assert all(item.status is RunStatus.COMPLETED for item in runs)
    # Conversation 维度过滤不影响其它会话。
    assert await manager.list_runs(conversation_id="conv-other") == ()


# ---------------------------------------------------------------------------
# 9. RunManager 不影响 Trace / Checkpoint 原有行为
# ---------------------------------------------------------------------------


async def test_run_manager_keeps_trace_and_checkpoint_behavior(
    manager_factory,
    tmp_path,
) -> None:
    from app.trace import SQLiteTraceEventHandler, SQLiteTraceStore

    build_manager = manager_factory
    trace_store = SQLiteTraceStore(tmp_path / "trace.db")
    await trace_store.initialize()
    trace_handler = SQLiteTraceEventHandler(trace_store)

    registry, _ = fake_registry(
        [
            model_response(
                tool_calls=(
                    ToolCall(id="count-1", name="count", arguments={"value": 1}),
                )
            ),
            model_response(content="完成"),
        ]
    )
    tool = CountingTool()
    tools = ToolRegistry()
    tools.register(tool)
    manager, _, checkpoint_store = await build_manager(registry, tools)

    run_id, _ = await manager.start(
        "执行工具",
        conversation_id="conv-1",
        event_handler=trace_handler,
    )
    run = await manager.wait(run_id)

    assert run.status is RunStatus.COMPLETED
    # Trace 行为不变：run 摘要 + 完整事件都已落库。
    trace = await trace_store.get(run_id)
    assert trace is not None
    assert trace.status.value == "completed"
    events = await trace_store.load_events(run_id)
    types = {event.type for event in events}
    assert AgentEventType.AGENT_STARTED in types
    assert AgentEventType.TOOL_STARTED in types
    assert AgentEventType.AGENT_COMPLETED in types
    # Checkpoint 行为不变：completed 工具结果被保留。
    checkpoint = await checkpoint_store.get(run_id)
    assert checkpoint is not None
    assert checkpoint.status is CheckpointStatus.COMPLETED
    assert [c.tool_call_id for c in checkpoint.completed_tool_results] == ["count-1"]
    # 工具只执行一次（Trace 与 Checkpoint 均不重复）。
    assert tool.executions == 1


# ---------------------------------------------------------------------------
# 10. start 返回 RUNNING、list 状态过滤
# ---------------------------------------------------------------------------


async def test_list_runs_status_filter(manager_factory) -> None:
    build_manager = manager_factory
    registry, _ = fake_registry(
        [
            model_response(content="完成"),
            model_response(content="完成"),
        ]
    )
    manager, _, _ = await build_manager(registry)

    first, _ = await manager.start("a", conversation_id="conv-1")
    second, _ = await manager.start("b", conversation_id="conv-1")
    await manager.wait(first)
    await manager.wait(second)

    completed = await manager.list_runs(status=RunStatus.COMPLETED)
    assert {item.id for item in completed} == {first, second}
    assert await manager.list_runs(status=RunStatus.RUNNING) == ()


# ---------------------------------------------------------------------------
# 11. 普通 start 不自动恢复（只有 recover 显式加载 Checkpoint）
# ---------------------------------------------------------------------------


async def test_plain_start_does_not_auto_recover_interrupted_checkpoint(
    manager_factory,
    tmp_path,
) -> None:
    build_manager = manager_factory
    # 预置一个中断 Run + 可恢复 Checkpoint（含已完成工具结果），与 build_manager
    # 共用同一个 database 文件（模拟进程重启后的持久化状态）。
    old_run = await _make_interrupted_run_with_checkpoint(tmp_path)
    old_run_id = old_run["run_id"]
    old_checkpoint_store = old_run["checkpoint_store"]

    registry, adapter = fake_registry([model_response(content="完成")])
    manager, _, checkpoint_store = await build_manager(registry)
    # 普通 start：不传 recovery_run_id。
    new_run_id, _ = await manager.start("继续", conversation_id="conv-1")
    new_run = await manager.wait(new_run_id)

    assert new_run.status is RunStatus.COMPLETED
    # 模型请求不应注入恢复证据。
    assert not any(
        message.name == CHECKPOINT_CONTEXT_MESSAGE_NAME
        for message in adapter.requests[0].messages
    )
    # 旧 Checkpoint 不应被 mark_recovered（普通 start 不处理它）。
    assert await old_checkpoint_store.get_unrecovered(old_run_id) is not None
    # 新 Run 不是恢复来源（无 recovered_from）。
    assert new_run.recovered_from_run_id is None


# ---------------------------------------------------------------------------
# 12. reconciliation 统一通过 RunManager（Checkpoint 层也被处理）
# ---------------------------------------------------------------------------


async def test_reconcile_also_marks_stale_checkpoints(
    manager_factory,
    tmp_path,
) -> None:
    build_manager = manager_factory
    # 构造一个只有 Checkpoint（无 Run 记录）的遗留 RUNNING Checkpoint。
    checkpoint_store = SQLiteCheckpointStore(tmp_path / "vesta.db")
    await checkpoint_store.initialize()
    await checkpoint_store.start(
        "orphan-cp",
        conversation_id="conv-9",
        user_message=Message(role=MessageRole.USER, content="遗留任务"),
    )

    registry, _ = fake_registry([model_response(content="完成")])
    manager, _, checkpoint_store2 = await build_manager(
        registry,
        checkpoint_store=checkpoint_store,
    )
    await manager.initialize()

    checkpoint = await checkpoint_store2.get("orphan-cp")
    assert checkpoint is not None
    assert checkpoint.status is CheckpointStatus.INTERRUPTED


# ---------------------------------------------------------------------------
# 辅助：构造一个中断 Run + 可恢复 Checkpoint
# ---------------------------------------------------------------------------


async def _make_interrupted_run_with_checkpoint(
    tmp_path,
) -> dict[str, object]:
    database = tmp_path / "vesta.db"
    run_store = SQLiteRunStore(database)
    checkpoint_store = SQLiteCheckpointStore(database)
    await run_store.initialize()
    await checkpoint_store.initialize()

    done = ToolCall(id="done-x", name="count", arguments={"value": 1})
    run = await run_store.create(conversation_id="conv-1", user_message="统计")
    await run_store.mark_started(run.id)
    await checkpoint_store.start(
        run.id,
        conversation_id="conv-1",
        user_message=Message(role=MessageRole.USER, content="统计"),
    )
    await checkpoint_store.before_model(run.id, step=1)
    await checkpoint_store.before_tools(run.id, step=1, tool_calls=(done,))
    await checkpoint_store.complete_tool(run.id, result=_tool_result(done))
    await checkpoint_store.interrupt(run.id, error="process stopped")
    return {
        "run_id": run.id,
        "run_store": run_store,
        "checkpoint_store": checkpoint_store,
        "database": str(database),
    }
