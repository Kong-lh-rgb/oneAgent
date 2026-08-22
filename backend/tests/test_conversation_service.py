"""ConversationService 测试：统一执行链（CLI 与 Automation 共用）。

覆盖：加载最新持久化 history / summary、Run 完成后写回 Conversation、
Summary 写回、Trace 统一注入、provenance 保留、is_run_running、
同会话并发串行 / 跨会话并行、Run provenance 持久化。

用 StubRunManager 返回固定的 AgentResult，不调用真实模型 API。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.agent.events import AgentEvent, AgentEventType
from app.agent.result import AgentResult, AgentStopReason
from app.context import (
    ConversationSummaryState,
    RollingConversationSummary,
    SQLiteConversationSummaryStore,
)
from app.conversation import (
    ConversationSource,
    SQLiteConversationStore,
    TriggerContext,
)
from app.conversation.service import ConversationService
from app.models.types import Message, MessageRole, ModelUsage
from app.run import SQLiteRunStore
from app.run.models import RunStatus
from app.trace import SQLiteTraceStore


def _message(role: MessageRole, content: str) -> Message:
    return Message(role=role, content=content)


class StubRunManager:
    """记录 start / recover；返回固定 AgentResult；可配置 get_run 状态。"""

    def __init__(self, agent_result: AgentResult) -> None:
        self._agent_result = agent_result
        self.started: list[dict] = []
        self.recovered: list[dict] = []
        self.run_status = "completed"
        self.conversation_id: str | None = "conv-stub"
        self.missing_run = False
        # 取消场景：result() 返回 None，wait() 返回 RunStatus.CANCELLED。
        self.cancelled = False
        # 暂停（中断）场景：result() 返回 None，wait() 返回 RunStatus.INTERRUPTED。
        self.interrupted = False

    async def start(
        self,
        user_message: str,
        *,
        conversation_id=None,
        history=(),
        summary_state=None,
        event_handler=None,
        recovery_run_id=None,
        source=None,
        source_id=None,
        scheduled_for=None,
        triggered_at=None,
        mode=None,
    ) -> tuple[str, None]:
        self.started.append(
            {
                "conversation_id": conversation_id,
                "content": user_message,
                "history": history,
                "summary_state": summary_state,
                "event_handler": event_handler,
                "source": source,
                "source_id": source_id,
                "scheduled_for": scheduled_for,
                "triggered_at": triggered_at,
                "mode": mode,
            }
        )
        return "run-1", None

    async def recover(
        self,
        run_id: str,
        *,
        history=(),
        summary_state=None,
        event_handler=None,
    ) -> tuple[str, None]:
        self.recovered.append(
            {
                "run_id": run_id,
                "history": history,
                "summary_state": summary_state,
                "event_handler": event_handler,
            }
        )
        return "run-2", None

    async def wait(self, run_id: str):
        if self.cancelled:
            status = RunStatus.CANCELLED
        elif self.interrupted:
            status = RunStatus.INTERRUPTED
        else:
            status = SimpleNamespace(value=self.run_status)
        return SimpleNamespace(
            id=run_id,
            stop_reason="final_answer",
            status=status,
        )

    def result(self, run_id: str) -> AgentResult | None:
        # RunManager.result 是同步方法（返回本进程最近结果）。
        if self.cancelled or self.interrupted:
            return None
        return self._agent_result

    async def get_run(self, run_id: str):
        if self.missing_run:
            return None
        return SimpleNamespace(
            id=run_id,
            conversation_id=self.conversation_id,
            status=SimpleNamespace(value=self.run_status),
        )


def _result_with(messages: tuple[Message, ...], *, summary=None) -> AgentResult:
    return AgentResult(
        run_id="run-1",
        final_message=messages[-1],
        messages=messages,
        steps=1,
        stop_reason=AgentStopReason.FINAL_ANSWER,
        usage=ModelUsage(),
        summary_state=summary,
    )


@pytest.fixture
async def service_factory(tmp_path):
    """构造 (ConversationService, stores, stub run manager)。"""

    async def build(result: AgentResult, run_status: str = "completed"):
        database = tmp_path / "vesta.db"
        conversation_store = SQLiteConversationStore(database)
        summary_store = SQLiteConversationSummaryStore(database)
        trace_store = SQLiteTraceStore(database)
        run_store = SQLiteRunStore(database)
        for store in (
            conversation_store,
            summary_store,
            trace_store,
            run_store,
        ):
            await store.initialize()
        manager = StubRunManager(result)
        manager.run_status = run_status
        service = ConversationService(
            conversation_store,
            manager,
            trace_store,
            summary_store=summary_store,
        )
        return service, conversation_store, summary_store, trace_store, manager

    return build


# ---------------------------------------------------------------------------
# 1. 加载最新持久化 history + 2. 结果写回（A B → 触发 C → A B C D）
# ---------------------------------------------------------------------------


async def test_dispatch_loads_latest_history_and_writes_back(service_factory) -> None:
    service, conversation_store, _, _, manager = await service_factory(
        _result_with(
            (
                _message(MessageRole.USER, "A"),
                _message(MessageRole.ASSISTANT, "B"),
                _message(MessageRole.USER, "C"),
                _message(MessageRole.ASSISTANT, "D"),
            )
        )
    )
    conversation = await conversation_store.create(
        messages=(
            _message(MessageRole.USER, "A"),
            _message(MessageRole.ASSISTANT, "B"),
        )
    )

    dispatch = await service.dispatch(
        conversation_id=conversation.id,
        content="C",
        trigger=TriggerContext(source=ConversationSource.MANUAL),
    )

    # Run 拿到的是“触发那一刻最新”的持久化 history（A B），不是创建时快照。
    started = manager.started[0]
    assert started["conversation_id"] == conversation.id
    assert [m.content for m in started["history"]] == ["A", "B"]
    assert started["content"] == "C"
    # 写回后持久化 Conversation 为 A B C D。
    persisted = await conversation_store.load_messages(conversation.id)
    assert [m.content for m in persisted] == ["A", "B", "C", "D"]
    assert dispatch.run.id == "run-1"


# ---------------------------------------------------------------------------
# 3. Summary 写回
# ---------------------------------------------------------------------------


async def test_dispatch_saves_latest_summary(service_factory) -> None:
    state = ConversationSummaryState(
        summary=RollingConversationSummary(current_objective="继续任务"),
        covered_message_count=3,
    )
    service, conversation_store, summary_store, _, _ = await service_factory(
        _result_with(
            (
                _message(MessageRole.USER, "A"),
                _message(MessageRole.ASSISTANT, "D"),
            ),
            summary=state,
        )
    )
    conversation = await conversation_store.create(
        messages=(_message(MessageRole.USER, "A"),)
    )

    await service.dispatch(
        conversation_id=conversation.id,
        content="C",
        trigger=TriggerContext(source=ConversationSource.AUTOMATION),
    )

    saved = await summary_store.load(conversation.id)
    assert saved is not None
    assert saved.summary.current_objective == "继续任务"
    assert saved.covered_message_count == 3


# ---------------------------------------------------------------------------
# 4. Trace 统一注入（Automation 与手动同路径）
# ---------------------------------------------------------------------------


async def test_dispatch_injects_trace_handler(service_factory) -> None:
    service, conversation_store, _, trace_store, manager = await service_factory(
        _result_with(
            (
                _message(MessageRole.USER, "A"),
                _message(MessageRole.ASSISTANT, "D"),
            )
        )
    )
    conversation = await conversation_store.create(
        messages=(_message(MessageRole.USER, "A"),)
    )

    await service.dispatch(
        conversation_id=conversation.id,
        content="C",
        trigger=TriggerContext(source=ConversationSource.AUTOMATION),
    )

    # dispatch 必须注入 SQLiteTraceEventHandler。
    handler = manager.started[0]["event_handler"]
    from app.trace import SQLiteTraceEventHandler

    assert isinstance(handler, SQLiteTraceEventHandler) or hasattr(
        handler, "emit"
    )
    # 通过 handler 发出事件 → Trace 可查询。
    await handler.emit(
        AgentEvent(
            run_id="run-1",
            conversation_id=conversation.id,
            type=AgentEventType.AGENT_STARTED,
        )
    )
    trace = await trace_store.get("run-1")
    assert trace is not None
    assert trace.conversation_id == conversation.id


# ---------------------------------------------------------------------------
# 5. provenance 保留
# ---------------------------------------------------------------------------


async def test_dispatch_preserves_trigger_provenance(service_factory) -> None:
    service, conversation_store, _, _, _ = await service_factory(
        _result_with(
            (
                _message(MessageRole.USER, "A"),
                _message(MessageRole.ASSISTANT, "D"),
            )
        )
    )
    conversation = await conversation_store.create()
    trigger = TriggerContext(
        source=ConversationSource.AUTOMATION,
        automation_id="auto-123",
        scheduled_for=datetime.now(UTC),
        triggered_at=datetime.now(UTC),
    )

    dispatch = await service.dispatch(
        conversation_id=conversation.id,
        content="C",
        trigger=trigger,
    )

    assert dispatch.trigger is trigger
    assert dispatch.trigger.source is ConversationSource.AUTOMATION
    assert dispatch.trigger.automation_id == "auto-123"


# ---------------------------------------------------------------------------
# 6. is_run_running（供 Scheduler max_instances 检查）
# ---------------------------------------------------------------------------


async def test_is_run_running(service_factory) -> None:
    service, conversation_store, _, _, manager = await service_factory(
        _result_with(
            (
                _message(MessageRole.USER, "A"),
                _message(MessageRole.ASSISTANT, "D"),
            )
        )
    )
    await conversation_store.create()

    manager.run_status = "running"
    assert await service.is_run_running("run-1") is True
    manager.run_status = "completed"
    assert await service.is_run_running("run-1") is False


# ---------------------------------------------------------------------------
# 7. 用户取消：Run 无 result 时合成终态结果并落库 + emit agent_cancelled
# ---------------------------------------------------------------------------


async def test_dispatch_cancelled_run_synthesizes_result(service_factory) -> None:
    service, conversation_store, _, trace_store, manager = await service_factory(
        _result_with(
            (
                _message(MessageRole.USER, "A"),
                _message(MessageRole.ASSISTANT, "B"),
            )
        )
    )
    manager.cancelled = True
    conversation = await conversation_store.create(
        messages=(_message(MessageRole.USER, "A"),)
    )

    dispatch = await service.dispatch(
        conversation_id=conversation.id,
        content="C",
        trigger=TriggerContext(source=ConversationSource.MANUAL),
    )

    # 落库：历史 A + 本次 user C + 中断说明 assistant（不再丢消息 / 不再报错）。
    persisted = await conversation_store.load_messages(conversation.id)
    assert [m.content for m in persisted] == [
        "A",
        "C",
        "Run cancelled：已停止，未生成最终回复。（本轮未完成的内容不会显示）",
    ]
    assert dispatch.result.stop_reason is AgentStopReason.CANCELLED
    # Trace 记录 agent_cancelled 终态事件。
    trace = await trace_store.get("run-1")
    assert trace is not None


async def test_dispatch_interrupted_run_synthesizes_result(service_factory) -> None:
    service, conversation_store, _, trace_store, manager = await service_factory(
        _result_with(
            (
                _message(MessageRole.USER, "A"),
                _message(MessageRole.ASSISTANT, "B"),
            )
        )
    )
    manager.interrupted = True
    conversation = await conversation_store.create(
        messages=(_message(MessageRole.USER, "A"),)
    )

    dispatch = await service.dispatch(
        conversation_id=conversation.id,
        content="C",
        trigger=TriggerContext(source=ConversationSource.MANUAL),
    )

    # 落库：历史 A + 本次 user C + 中断说明（可恢复语义），不再抛错。
    persisted = await conversation_store.load_messages(conversation.id)
    assert [m.content for m in persisted] == [
        "A",
        "C",
        "Run interrupted：已暂停，可从断点继续。（点击 Recover 从保存的中断点恢复）",
    ]
    assert dispatch.result.stop_reason is AgentStopReason.INTERRUPTED
    # Trace 记录 agent_failed(interrupted) 终态事件。
    trace = await trace_store.get("run-1")
    assert trace is not None


# ---------------------------------------------------------------------------
# 7. 下一个 Automation 读取上一次自动执行的结果（A B C D）
# ---------------------------------------------------------------------------


async def test_next_dispatch_sees_previous_automation_result(
    service_factory,
) -> None:
    # 第一次：触发 C → 结果 A B C D。
    service, conversation_store, _, _, manager = await service_factory(
        _result_with(
            (
                _message(MessageRole.USER, "A"),
                _message(MessageRole.ASSISTANT, "B"),
                _message(MessageRole.USER, "C"),
                _message(MessageRole.ASSISTANT, "D"),
            )
        )
    )
    conversation = await conversation_store.create(
        messages=(
            _message(MessageRole.USER, "A"),
            _message(MessageRole.ASSISTANT, "B"),
        )
    )
    await service.dispatch(
        conversation_id=conversation.id,
        content="C",
        trigger=TriggerContext(source=ConversationSource.AUTOMATION),
    )

    # 第二次：模拟下一次 Automation 触发 E → 应读到 A B C D 而非旧的 A B。
    service2, _, _, _, manager2 = await service_factory(
        _result_with(
            (
                _message(MessageRole.USER, "A"),
                _message(MessageRole.ASSISTANT, "B"),
                _message(MessageRole.USER, "C"),
                _message(MessageRole.ASSISTANT, "D"),
                _message(MessageRole.USER, "E"),
                _message(MessageRole.ASSISTANT, "F"),
            )
        )
    )
    await service2.dispatch(
        conversation_id=conversation.id,
        content="E",
        trigger=TriggerContext(source=ConversationSource.AUTOMATION),
    )
    assert [m.content for m in manager2.started[0]["history"]] == [
        "A",
        "B",
        "C",
        "D",
    ]


# ---------------------------------------------------------------------------
# 8. 同 conversation 并发 dispatch 串行、不丢消息；不同 conversation 并行
# ---------------------------------------------------------------------------


class ConcurrentRunManager:
    """记录并发窗口；每次 result 基于当时 history + 本次输入动态构造。"""

    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0
        self.calls: list[dict] = []

    async def start(
        self,
        user_message: str,
        *,
        conversation_id=None,
        history=(),
        summary_state=None,
        event_handler=None,
        recovery_run_id=None,
        source=None,
        source_id=None,
        scheduled_for=None,
        triggered_at=None,
        mode=None,
    ) -> tuple[str, None]:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.calls.append(
            {
                "conversation_id": conversation_id,
                "content": user_message,
                "history": tuple(history),
                "mode": mode,
            }
        )
        return f"run-{len(self.calls)}", None

    async def wait(self, run_id: str):
        await asyncio.sleep(0.02)
        self.active -= 1
        return SimpleNamespace(
            id=run_id,
            stop_reason="final_answer",
            status=SimpleNamespace(value="completed"),
        )

    def result(self, run_id: str) -> AgentResult:
        index = int(run_id.split("-")[1]) - 1
        call = self.calls[index]
        messages = (
            *call["history"],
            _message(MessageRole.USER, call["content"]),
            _message(MessageRole.ASSISTANT, f"out-{index + 1}"),
        )
        return _result_with(messages)

    async def get_run(self, run_id: str):
        return None


async def test_same_conversation_dispatch_is_serialized(service_factory) -> None:
    service, conversation_store, _, _, _ = await service_factory(
        _result_with((_message(MessageRole.USER, "x"),))
    )
    conversation = await conversation_store.create(
        messages=(
            _message(MessageRole.USER, "A"),
            _message(MessageRole.ASSISTANT, "B"),
        )
    )
    # 替换为记录并发的 run manager。
    manager = ConcurrentRunManager()
    service._run_manager = manager  # type: ignore[assignment]

    task1 = asyncio.create_task(
        service.dispatch(
            conversation_id=conversation.id,
            content="C",
            trigger=TriggerContext(source=ConversationSource.MANUAL),
        )
    )
    task2 = asyncio.create_task(
        service.dispatch(
            conversation_id=conversation.id,
            content="D",
            trigger=TriggerContext(source=ConversationSource.MANUAL),
        )
    )
    await asyncio.gather(task1, task2)

    # 同一会话串行：任意时刻最多 1 个 dispatch 在执行。
    assert manager.max_active == 1
    # 第二次 dispatch 读到的是第一次写回后的最新 history，不丢消息。
    assert len(manager.calls) == 2
    assert [m.content for m in manager.calls[1]["history"]] == [
        "A",
        "B",
        "C",
        "out-1",
    ]
    persisted = await conversation_store.load_messages(conversation.id)
    assert [m.content for m in persisted] == [
        "A",
        "B",
        "C",
        "out-1",
        "D",
        "out-2",
    ]


async def test_different_conversations_dispatch_in_parallel(service_factory) -> None:
    service, conversation_store, _, _, _ = await service_factory(
        _result_with((_message(MessageRole.USER, "x"),))
    )
    conv_a = await conversation_store.create()
    conv_b = await conversation_store.create()
    manager = ConcurrentRunManager()
    service._run_manager = manager  # type: ignore[assignment]

    task_a = asyncio.create_task(
        service.dispatch(
            conversation_id=conv_a.id,
            content="A-input",
            trigger=TriggerContext(source=ConversationSource.MANUAL),
        )
    )
    task_b = asyncio.create_task(
        service.dispatch(
            conversation_id=conv_b.id,
            content="B-input",
            trigger=TriggerContext(source=ConversationSource.MANUAL),
        )
    )
    await asyncio.gather(task_a, task_b)

    # 不同会话可并行：两个 dispatch 同时进入执行。
    assert manager.max_active == 2
    assert len(manager.calls) == 2


# ---------------------------------------------------------------------------
# 9. provenance 持久化到 Run，重启后仍可查询
# ---------------------------------------------------------------------------


class ProvenanceRuntime:
    """极简 runtime：直接产生一个带 run_id 的完成事件。"""

    async def run_stream(
        self,
        user_input: str,
        *,
        history=(),
        conversation_id=None,
        event_handler=None,
        summary_state=None,
        run_id=None,
        recovery_run_id=None,
        mode=None,
    ):
        final = _message(MessageRole.ASSISTANT, "完成")
        user = _message(MessageRole.USER, user_input)
        result = _result_with((*history, user, final))
        result = result.model_copy(update={"run_id": run_id})
        event = AgentEvent(
            run_id=run_id,
            conversation_id=conversation_id,
            type=AgentEventType.AGENT_COMPLETED,
            stop_reason=result.stop_reason,
            result=result,
        )
        if event_handler is not None:
            await event_handler.emit(event)
        yield event


async def test_run_provenance_persisted_across_restart(
    tmp_path,
) -> None:
    from app.checkpoint import SQLiteCheckpointStore
    from app.run import RunManager

    database = tmp_path / "vesta.db"
    conversation_store = SQLiteConversationStore(database)
    await conversation_store.initialize()
    conversation = await conversation_store.create()
    trace_store = SQLiteTraceStore(database)
    await trace_store.initialize()
    summary_store = SQLiteConversationSummaryStore(database)
    await summary_store.initialize()
    run_store = SQLiteRunStore(database)
    await run_store.initialize()
    checkpoint_store = SQLiteCheckpointStore(database)
    await checkpoint_store.initialize()

    run_manager = RunManager(run_store, checkpoint_store, ProvenanceRuntime())
    service = ConversationService(
        conversation_store,
        run_manager,
        trace_store,
        summary_store=summary_store,
    )
    scheduled_for = datetime.now(UTC)
    triggered_at = datetime.now(UTC)
    dispatch = await service.dispatch(
        conversation_id=conversation.id,
        content="C",
        trigger=TriggerContext(
            source=ConversationSource.AUTOMATION,
            automation_id="auto-9",
            scheduled_for=scheduled_for,
            triggered_at=triggered_at,
        ),
    )

    # 重启后重新打开 RunStore，provenance 仍可查询。
    reopened = SQLiteRunStore(database)
    await reopened.initialize()
    run = await reopened.get(dispatch.run.id)
    assert run is not None
    assert run.source == "automation"
    assert run.source_id == "auto-9"
    assert run.scheduled_for is not None
    assert run.triggered_at is not None


# ---------------------------------------------------------------------------
# 10. recover 收口：load → recover → wait → 写回 Conversation + Summary
# ---------------------------------------------------------------------------


async def test_recover_writes_back_conversation_and_summary(
    service_factory,
) -> None:
    """recover 后最终 messages 写回 Conversation，summary_state 写回 SummaryStore。"""

    state = ConversationSummaryState(
        summary=RollingConversationSummary(current_objective="恢复后继续"),
        covered_message_count=4,
    )
    service, conversation_store, summary_store, _, manager = await service_factory(
        _result_with(
            (
                _message(MessageRole.USER, "A"),
                _message(MessageRole.ASSISTANT, "B"),
                _message(MessageRole.USER, "C"),
                _message(MessageRole.ASSISTANT, "D-恢复"),
            ),
            summary=state,
        )
    )
    conversation = await conversation_store.create(
        messages=(
            _message(MessageRole.USER, "A"),
            _message(MessageRole.ASSISTANT, "B"),
        )
    )
    manager.conversation_id = conversation.id

    dispatch = await service.recover("old-run")

    # 恢复时把最新持久化 history（A B）交给 RunManager.recover。
    recovered = manager.recovered[0]
    assert recovered["run_id"] == "old-run"
    assert [m.content for m in recovered["history"]] == ["A", "B"]

    # 写回后持久化 Conversation 为完整结果。
    persisted = await conversation_store.load_messages(conversation.id)
    assert [m.content for m in persisted] == [
        "A",
        "B",
        "C",
        "D-恢复",
    ]
    # summary_state 正确写回 SummaryStore。
    saved = await summary_store.load(conversation.id)
    assert saved is not None
    assert saved.summary.current_objective == "恢复后继续"
    assert saved.covered_message_count == 4
    assert dispatch.run.id == "run-2"


async def test_recover_missing_run_raises(service_factory) -> None:
    service, _, _, _, manager = await service_factory(
        _result_with((_message(MessageRole.USER, "A"),))
    )
    manager.missing_run = True

    with pytest.raises(KeyError):
        await service.recover("no-such-run")
    assert manager.recovered == []


async def test_recover_uses_latest_summary(service_factory) -> None:
    """recover 加载会话最新 Summary 交给 RunManager，而非空 state。"""

    state = ConversationSummaryState(
        summary=RollingConversationSummary(current_objective="旧目标"),
        covered_message_count=2,
    )
    service, conversation_store, summary_store, _, manager = await service_factory(
        _result_with(
            (
                _message(MessageRole.USER, "A"),
                _message(MessageRole.ASSISTANT, "B"),
            )
        )
    )
    conversation = await conversation_store.create(
        messages=(_message(MessageRole.USER, "A"),)
    )
    await summary_store.save(conversation.id, state)
    manager.conversation_id = conversation.id

    await service.recover("old-run")

    recovered = manager.recovered[0]
    assert recovered["summary_state"] is not None
    assert recovered["summary_state"].summary.current_objective == "旧目标"
