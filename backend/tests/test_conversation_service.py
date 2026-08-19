"""ConversationService 测试：统一执行链（CLI 与 Automation 共用）。

覆盖：加载最新持久化 history / summary、Run 完成后写回 Conversation、
Summary 写回、Trace 统一注入、provenance 保留、is_run_running。

用 StubRunManager 返回固定的 AgentResult，不调用真实模型 API。
"""

from __future__ import annotations

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
from app.trace import SQLiteTraceStore


def _message(role: MessageRole, content: str) -> Message:
    return Message(role=role, content=content)


class StubRunManager:
    """记录 start；返回固定 AgentResult；可配置 get_run 状态。"""

    def __init__(self, agent_result: AgentResult) -> None:
        self._agent_result = agent_result
        self.started: list[dict] = []
        self.run_status = "completed"

    async def start(
        self,
        user_message: str,
        *,
        conversation_id=None,
        history=(),
        summary_state=None,
        event_handler=None,
        recovery_run_id=None,
    ) -> tuple[str, None]:
        self.started.append(
            {
                "conversation_id": conversation_id,
                "content": user_message,
                "history": history,
                "summary_state": summary_state,
                "event_handler": event_handler,
            }
        )
        return "run-1", None

    async def wait(self, run_id: str):
        return SimpleNamespace(
            id=run_id,
            stop_reason="final_answer",
            status=SimpleNamespace(value=self.run_status),
        )

    def result(self, run_id: str) -> AgentResult:
        # RunManager.result 是同步方法（返回本进程最近结果）。
        return self._agent_result

    async def get_run(self, run_id: str):
        return SimpleNamespace(status=SimpleNamespace(value=self.run_status))


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
        database = tmp_path / "oneagent.db"
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
