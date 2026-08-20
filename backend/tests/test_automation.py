"""Automation / Scheduler V1 测试（Scheduler 经 ConversationService 投递输入）。

覆盖：一次性 / 重复任务触发、状态管理、重启恢复、misfire/coalesce、
并发保护、工具校验、provenance 投递、状态机非法转换、completed/cancelled
不再执行。

Scheduler 不再直接启动 Run —— 用 FakeConversationService 记录 dispatch 投递，
不调用真实模型 API；用可控时间避免真实等待。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.automation.models import (
    AutomationStatus,
    Schedule,
    ScheduleKind,
)
from app.automation.scheduler import AutomationScheduler
from app.automation.store import SQLiteAutomationStore
from app.automation.tools import (
    AutomationCreateTool,
    build_schedule_and_next,
)
from app.conversation import ConversationSource

_USER_MESSAGE = "提醒我交作业"


class _FakeRunRef:
    def __init__(self, run_id: str) -> None:
        self.id = run_id


class _FakeDispatch:
    def __init__(self, run_id: str) -> None:
        self.run = _FakeRunRef(run_id)


class FakeConversationService:
    """记录 dispatch 投递；可配置上一次 Run 是否仍在执行。"""

    def __init__(self) -> None:
        self.dispatched: list[dict] = []
        self.running_run_ids: set[str] = set()
        self.fail_on_dispatch: bool = False

    async def dispatch(
        self,
        *,
        conversation_id=None,
        content: str,
        trigger=None,
        event_handler=None,
        on_run_started=None,
    ) -> _FakeDispatch:
        if self.fail_on_dispatch:
            raise RuntimeError("dispatch failed")
        run_id = f"run-{len(self.dispatched) + 1}"
        self.dispatched.append(
            {
                "run_id": run_id,
                "conversation_id": conversation_id,
                "content": content,
                "trigger": trigger,
            }
        )
        self.running_run_ids.add(run_id)
        if on_run_started is not None:
            await on_run_started(run_id)
        return _FakeDispatch(run_id)

    async def is_run_running(self, run_id: str) -> bool:
        return run_id in self.running_run_ids

    def finish_run(self, run_id: str) -> None:
        self.running_run_ids.discard(run_id)


class CrashAfterStartService(FakeConversationService):
    """模拟 Run 创建（on_run_started 已被调用）后、完成前进程崩溃。"""

    async def dispatch(
        self,
        *,
        conversation_id=None,
        content: str,
        trigger=None,
        event_handler=None,
        on_run_started=None,
    ):
        run_id = "run-crash"
        if on_run_started is not None:
            await on_run_started(run_id)
        raise RuntimeError("process crashed after run started")


@pytest.fixture
async def make_scheduler(tmp_path):
    """构造 (store, scheduler, fake service)；测试结束后统一 shutdown。"""

    schedulers: list[AutomationScheduler] = []

    async def _make(
        service: FakeConversationService | None = None,
    ) -> tuple[SQLiteAutomationStore, AutomationScheduler, FakeConversationService]:
        store = SQLiteAutomationStore(tmp_path / "vesta.db")
        await store.initialize()
        service = service or FakeConversationService()
        scheduler = AutomationScheduler(store, service)
        schedulers.append(scheduler)
        return store, scheduler, service

    yield _make

    for scheduler in schedulers:
        try:
            await scheduler.shutdown()
        except Exception:  # noqa: BLE001
            pass


def _future(days: int = 1, hours: int = 0) -> datetime:
    return datetime.now(UTC) + timedelta(days=days, hours=hours)


def _past(hours: int = 2) -> datetime:
    return datetime.now(UTC) - timedelta(hours=hours)


# ---------------------------------------------------------------------------
# 1. 创建一次性 Automation + 2/3. 到点投递 + conversation 关联
# ---------------------------------------------------------------------------


async def test_create_once_automation_and_trigger_dispatch(make_scheduler) -> None:
    store, scheduler, service = await make_scheduler()
    schedule = Schedule(
        kind=ScheduleKind.ONCE,
        run_at=_future(hours=1),
        timezone="UTC",
    )
    automation = await scheduler.create_automation(
        title="交作业提醒",
        prompt=_USER_MESSAGE,
        conversation_id="conv-1",
        schedule=schedule,
        next_run_at=schedule.run_at,
    )

    assert automation.status is AutomationStatus.ACTIVE
    assert automation.next_run_at is not None
    assert automation.next_run_at > datetime.now(UTC)

    await scheduler._trigger(automation.id)

    assert len(service.dispatched) == 1
    dispatched = service.dispatched[0]
    assert dispatched["content"] == _USER_MESSAGE
    assert dispatched["conversation_id"] == "conv-1"


# ---------------------------------------------------------------------------
# 4. 一次性任务触发后 COMPLETED
# ---------------------------------------------------------------------------


async def test_once_automation_becomes_completed_after_trigger(make_scheduler) -> None:
    store, scheduler, _ = await make_scheduler()
    schedule = Schedule(
        kind=ScheduleKind.ONCE,
        run_at=_future(hours=1),
        timezone="UTC",
    )
    automation = await scheduler.create_automation(
        title="一次性",
        prompt="做一件事",
        conversation_id="conv-1",
        schedule=schedule,
        next_run_at=schedule.run_at,
    )

    await scheduler._trigger(automation.id)

    updated = await store.get(automation.id)
    assert updated is not None
    assert updated.status is AutomationStatus.COMPLETED
    assert updated.last_run_id is not None
    assert updated.next_run_at is None


# ---------------------------------------------------------------------------
# 5/6. 重复任务触发后仍 ACTIVE，next_run_at 正确更新
# ---------------------------------------------------------------------------


async def test_interval_automation_stays_active_and_updates_next(
    make_scheduler,
) -> None:
    store, scheduler, _ = await make_scheduler()
    schedule = Schedule(
        kind=ScheduleKind.INTERVAL,
        interval_seconds=3600,
        timezone="UTC",
    )
    automation = await scheduler.create_automation(
        title="每小时总结",
        prompt="总结进度",
        conversation_id="conv-1",
        schedule=schedule,
        next_run_at=_future(hours=1),
    )
    old_next = automation.next_run_at

    await scheduler._trigger(automation.id)

    updated = await store.get(automation.id)
    assert updated is not None
    assert updated.status is AutomationStatus.ACTIVE
    assert updated.last_run_id is not None
    assert updated.next_run_at is not None
    assert updated.next_run_at > datetime.now(UTC)
    assert updated.next_run_at != old_next


# ---------------------------------------------------------------------------
# 7/8/9. pause / resume / cancel
# ---------------------------------------------------------------------------


async def test_pause_resume_cancel_lifecycle(make_scheduler) -> None:
    store, scheduler, service = await make_scheduler()
    schedule = Schedule(
        kind=ScheduleKind.INTERVAL,
        interval_seconds=3600,
        timezone="UTC",
    )
    automation = await scheduler.create_automation(
        title="定时任务",
        prompt="执行",
        conversation_id="conv-1",
        schedule=schedule,
        next_run_at=_future(hours=1),
    )

    paused = await scheduler.pause(automation.id)
    assert paused.status is AutomationStatus.PAUSED
    await scheduler._trigger(automation.id)
    assert service.dispatched == []

    resumed = await scheduler.resume(automation.id)
    assert resumed.status is AutomationStatus.ACTIVE
    await scheduler._trigger(automation.id)
    assert len(service.dispatched) == 1

    cancelled = await scheduler.cancel(automation.id)
    assert cancelled.status is AutomationStatus.CANCELLED
    await scheduler._trigger(automation.id)
    assert len(service.dispatched) == 1
    with pytest.raises(ValueError, match="only active"):
        await scheduler.pause(automation.id)


# ---------------------------------------------------------------------------
# 10. 程序重启后 ACTIVE Automation 被重新加载
# ---------------------------------------------------------------------------


async def test_restart_reloads_active_automations(tmp_path) -> None:
    store = SQLiteAutomationStore(tmp_path / "vesta.db")
    await store.initialize()
    service = FakeConversationService()
    schedule = Schedule(
        kind=ScheduleKind.INTERVAL,
        interval_seconds=3600,
        timezone="UTC",
    )
    automation = await store.create(
        title="重启后任务",
        prompt="继续",
        conversation_id="conv-1",
        schedule=schedule,
        next_run_at=_future(hours=2),
    )

    restarted = AutomationScheduler(store, service)
    try:
        await restarted.start()
        jobs = restarted._scheduler.get_jobs()
        assert any(job.id == f"automation-{automation.id}" for job in jobs)
    finally:
        await restarted.shutdown()


# ---------------------------------------------------------------------------
# 11. 错过一次性任务只补跑一次
# ---------------------------------------------------------------------------


async def test_missed_once_automation_runs_only_once(tmp_path) -> None:
    store = SQLiteAutomationStore(tmp_path / "vesta.db")
    await store.initialize()
    service = FakeConversationService()
    schedule = Schedule(
        kind=ScheduleKind.ONCE,
        run_at=_past(),
        timezone="UTC",
    )
    automation = await store.create(
        title="错过的一次性",
        prompt="补跑",
        conversation_id="conv-1",
        schedule=schedule,
        next_run_at=_past(hours=3),
    )

    scheduler = AutomationScheduler(store, service)
    try:
        await scheduler._restore(automation)
        updated = await store.get(automation.id)
        assert updated is not None
        assert updated.status is AutomationStatus.ACTIVE
        assert updated.next_run_at is not None
        assert updated.next_run_at <= datetime.now(UTC) + timedelta(seconds=5)

        await scheduler._trigger(automation.id)
        assert len(service.dispatched) == 1
        completed = await store.get(automation.id)
        assert completed is not None
        assert completed.status is AutomationStatus.COMPLETED

        await scheduler._trigger(automation.id)
        assert len(service.dispatched) == 1
    finally:
        await scheduler.shutdown()


# ---------------------------------------------------------------------------
# 12. 重复任务 misfire 不批量补跑
# ---------------------------------------------------------------------------


async def test_recurring_misfire_does_not_batch_catchup(tmp_path) -> None:
    store = SQLiteAutomationStore(tmp_path / "vesta.db")
    await store.initialize()
    service = FakeConversationService()
    schedule = Schedule(
        kind=ScheduleKind.INTERVAL,
        interval_seconds=3600,
        timezone="UTC",
    )
    automation = await store.create(
        title="每小时任务",
        prompt="执行",
        conversation_id="conv-1",
        schedule=schedule,
        next_run_at=_past(hours=5),
    )

    scheduler = AutomationScheduler(store, service)
    try:
        await scheduler.start()
        updated = await store.get(automation.id)
        assert updated is not None
        assert updated.next_run_at is not None
        assert updated.next_run_at > datetime.now(UTC)
        assert service.dispatched == []
        jobs = scheduler._scheduler.get_jobs()
        automation_jobs = [
            job for job in jobs if job.id == f"automation-{automation.id}"
        ]
        assert len(automation_jobs) == 1
    finally:
        await scheduler.shutdown()


# ---------------------------------------------------------------------------
# 13. 不允许重叠无限执行（max_instances=1）
# ---------------------------------------------------------------------------


async def test_no_overlap_when_previous_run_still_running(make_scheduler) -> None:
    store, scheduler, service = await make_scheduler()
    schedule = Schedule(
        kind=ScheduleKind.INTERVAL,
        interval_seconds=3600,
        timezone="UTC",
    )
    automation = await scheduler.create_automation(
        title="每小时任务",
        prompt="执行",
        conversation_id="conv-1",
        schedule=schedule,
        next_run_at=_future(hours=1),
    )

    await scheduler._trigger(automation.id)
    assert len(service.dispatched) == 1
    refreshed = await store.get(automation.id)
    assert refreshed is not None and refreshed.last_run_id is not None

    # 上一次 Run 仍在执行 → 跳过。
    await scheduler._trigger(automation.id)
    assert len(service.dispatched) == 1

    # Run 结束后，下一次触发正常投递。
    service.finish_run(refreshed.last_run_id)
    await scheduler._trigger(automation.id)
    assert len(service.dispatched) == 2


# ---------------------------------------------------------------------------
# 14. 单个 Automation 投递失败不崩 Scheduler
# ---------------------------------------------------------------------------


async def test_dispatch_failure_does_not_crash_scheduler(make_scheduler) -> None:
    store, scheduler, service = await make_scheduler()
    schedule = Schedule(
        kind=ScheduleKind.INTERVAL,
        interval_seconds=3600,
        timezone="UTC",
    )
    automation = await scheduler.create_automation(
        title="失败任务",
        prompt="执行",
        conversation_id="conv-1",
        schedule=schedule,
        next_run_at=_future(hours=1),
    )

    service.fail_on_dispatch = True
    await scheduler._job_func(automation.id)()
    service.fail_on_dispatch = False

    await scheduler._trigger(automation.id)
    assert len(service.dispatched) == 1


# ---------------------------------------------------------------------------
# 15. automation_create Tool 参数校验
# ---------------------------------------------------------------------------


def test_automation_create_argument_validation() -> None:
    with pytest.raises(ValueError, match="kind"):
        build_schedule_and_next({"kind": "hourly", "prompt": "x", "title": "t"})
    with pytest.raises(ValueError, match="run_at"):
        build_schedule_and_next({"kind": "once", "prompt": "x", "title": "t"})
    with pytest.raises(ValueError, match="timezone offset"):
        build_schedule_and_next(
            {
                "kind": "once",
                "run_at": "2026-08-20T09:00:00",
                "prompt": "x",
                "title": "t",
            }
        )
    with pytest.raises(ValueError, match="future"):
        build_schedule_and_next(
            {
                "kind": "once",
                "run_at": "2020-01-01T00:00:00+08:00",
                "prompt": "x",
                "title": "t",
            }
        )
    with pytest.raises(ValueError, match="interval_seconds"):
        build_schedule_and_next(
            {"kind": "interval", "interval_seconds": 0, "prompt": "x", "title": "t"}
        )
    with pytest.raises(ValueError, match="interval_seconds"):
        build_schedule_and_next({"kind": "interval", "prompt": "x", "title": "t"})
    with pytest.raises(ValueError, match="cron_expr"):
        build_schedule_and_next(
            {"kind": "cron", "cron_expr": "not a cron", "prompt": "x", "title": "t"}
        )
    with pytest.raises(ValueError, match="timezone"):
        build_schedule_and_next(
            {
                "kind": "interval",
                "interval_seconds": 60,
                "timezone": "Mars/Olympus",
                "prompt": "x",
                "title": "t",
            }
        )


def test_automation_create_valid_schedules() -> None:
    schedule, next_run = build_schedule_and_next(
        {
            "kind": "once",
            "run_at": "2099-08-20T09:00:00+08:00",
            "prompt": "x",
            "title": "t",
        }
    )
    assert schedule.kind is ScheduleKind.ONCE
    assert next_run.tzinfo is not None
    schedule, next_run = build_schedule_and_next(
        {"kind": "interval", "interval_seconds": 7200, "prompt": "x", "title": "t"}
    )
    assert schedule.kind is ScheduleKind.INTERVAL
    assert next_run > datetime.now(UTC)
    schedule, next_run = build_schedule_and_next(
        {
            "kind": "cron",
            "cron_expr": "0 9 * * *",
            "timezone": "Asia/Shanghai",
            "prompt": "x",
            "title": "t",
        }
    )
    assert schedule.kind is ScheduleKind.CRON
    assert schedule.timezone == "Asia/Shanghai"
    assert next_run > datetime.now(UTC)


def test_automation_create_prompt_excludes_schedule_rule() -> None:
    """automation_create 的 schema 明确：prompt 只保存触发时真正要执行的指令，
    调度条件必须放进 schedule 字段，不能含在 prompt 里（避免触发后模型再次
    创建新自动化）。只校验说明文字，不新增自然语言解析。"""

    definition = AutomationCreateTool(scheduler=None).definition
    tool_desc = definition.description
    prompt_desc = definition.parameters["properties"]["prompt"]["description"]

    # Tool 说明：prompt 不能包含调度条件 + 示例拆解 + 后果说明。
    assert "不能包含" in tool_desc and "调度条件" in tool_desc
    assert "总结项目进度" in tool_desc
    assert "再次创建" in tool_desc

    # prompt 参数说明：只含执行内容、不含调度条件，并给出同一示例。
    assert "不含调度条件" in prompt_desc
    assert "总结项目进度" in prompt_desc
    assert "再次创建" in prompt_desc


# ---------------------------------------------------------------------------
# 16. provenance：Automation 触发携带 source / automation_id
# ---------------------------------------------------------------------------


async def test_automation_dispatch_carries_provenance(make_scheduler) -> None:
    store, scheduler, service = await make_scheduler()
    schedule = Schedule(
        kind=ScheduleKind.INTERVAL,
        interval_seconds=3600,
        timezone="UTC",
    )
    automation = await scheduler.create_automation(
        title="带来源",
        prompt="继续",
        conversation_id="conv-9",
        schedule=schedule,
        next_run_at=_future(hours=1),
    )

    await scheduler._trigger(automation.id)

    assert len(service.dispatched) == 1
    trigger = service.dispatched[0]["trigger"]
    assert trigger is not None
    assert trigger.source is ConversationSource.AUTOMATION
    assert trigger.automation_id == automation.id
    assert trigger.scheduled_for is not None
    assert trigger.triggered_at is not None


# ---------------------------------------------------------------------------
# 17. 状态机非法转换被拒绝；completed/cancelled 不再执行
# ---------------------------------------------------------------------------


async def test_invalid_automation_transitions_rejected(make_scheduler) -> None:
    store, scheduler, service = await make_scheduler()
    schedule = Schedule(
        kind=ScheduleKind.INTERVAL,
        interval_seconds=3600,
        timezone="UTC",
    )
    automation = await scheduler.create_automation(
        title="状态机",
        prompt="执行",
        conversation_id="conv-1",
        schedule=schedule,
        next_run_at=_future(hours=1),
    )

    await scheduler.cancel(automation.id)
    with pytest.raises(ValueError, match="invalid automation transition"):
        await store.update_status(automation.id, AutomationStatus.ACTIVE)
    with pytest.raises(ValueError, match="invalid automation transition"):
        await store.update_status(automation.id, AutomationStatus.COMPLETED)

    second = Schedule(
        kind=ScheduleKind.ONCE,
        run_at=_future(hours=2),
        timezone="UTC",
    )
    once = await scheduler.create_automation(
        title="一次性状态机",
        prompt="执行",
        conversation_id="conv-1",
        schedule=second,
        next_run_at=second.run_at,
    )
    await scheduler._trigger(once.id)
    assert (await store.get(once.id)).status is AutomationStatus.COMPLETED
    with pytest.raises(ValueError, match="invalid automation transition"):
        await store.update_status(once.id, AutomationStatus.CANCELLED)
    with pytest.raises(ValueError, match="invalid automation transition"):
        await store.update_status(once.id, AutomationStatus.PAUSED)


async def test_completed_and_cancelled_automations_do_not_dispatch(
    make_scheduler,
) -> None:
    store, scheduler, service = await make_scheduler()
    once_schedule = Schedule(
        kind=ScheduleKind.ONCE,
        run_at=_future(hours=1),
        timezone="UTC",
    )
    once = await scheduler.create_automation(
        title="已完成",
        prompt="执行",
        conversation_id="conv-1",
        schedule=once_schedule,
        next_run_at=once_schedule.run_at,
    )
    await scheduler._trigger(once.id)
    assert len(service.dispatched) == 1
    await scheduler._trigger(once.id)
    assert len(service.dispatched) == 1

    interval_schedule = Schedule(
        kind=ScheduleKind.INTERVAL,
        interval_seconds=3600,
        timezone="UTC",
    )
    cancelled = await scheduler.create_automation(
        title="已取消",
        prompt="执行",
        conversation_id="conv-1",
        schedule=interval_schedule,
        next_run_at=_future(hours=1),
    )
    await scheduler.cancel(cancelled.id)
    await scheduler._trigger(cancelled.id)
    assert len(service.dispatched) == 1


# ---------------------------------------------------------------------------
# 18. 真实异步集成：不手动调 _trigger，事件循环正常运行时 Scheduler 自动触发
# ---------------------------------------------------------------------------


class _IntegrationRunManager:
    """真实 ConversationService 用的极简 Run 管理器（返回固定结果）。"""

    def __init__(self) -> None:
        self.started: list[str] = []
        self._result = _integration_result()

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
        self.started.append(user_message)
        return "run-auto", None

    async def wait(self, run_id: str):
        return SimpleNamespace(
            id=run_id,
            stop_reason="final_answer",
            status=SimpleNamespace(value="completed"),
        )

    def result(self, run_id: str):
        return self._result

    async def get_run(self, run_id: str):
        return None


def _integration_result():
    from app.agent.result import AgentResult, AgentStopReason
    from app.models.types import Message, MessageRole, ModelUsage

    final = Message(role=MessageRole.ASSISTANT, content="自动完成")
    return AgentResult(
        run_id="run-auto",
        final_message=final,
        messages=(Message(role=MessageRole.USER, content="自动"), final),
        steps=1,
        stop_reason=AgentStopReason.FINAL_ANSWER,
        usage=ModelUsage(),
    )


async def test_scheduler_auto_triggers_without_manual_trigger(tmp_path) -> None:
    import asyncio

    from app.context import SQLiteConversationSummaryStore
    from app.conversation.service import ConversationService
    from app.conversation.store import SQLiteConversationStore
    from app.run import SQLiteRunStore
    from app.trace import SQLiteTraceStore

    database = tmp_path / "vesta.db"
    conversation_store = SQLiteConversationStore(database)
    await conversation_store.initialize()
    conversation = await conversation_store.create()
    trace_store = SQLiteTraceStore(database)
    await trace_store.initialize()
    summary_store = SQLiteConversationSummaryStore(database)
    await summary_store.initialize()
    await SQLiteRunStore(database).initialize()

    manager = _IntegrationRunManager()
    service = ConversationService(
        conversation_store,
        manager,
        trace_store,
        summary_store=summary_store,
    )
    store = SQLiteAutomationStore(database)
    await store.initialize()
    scheduler = AutomationScheduler(store, service)

    run_at = datetime.now(UTC) + timedelta(milliseconds=50)
    schedule = Schedule(
        kind=ScheduleKind.ONCE,
        run_at=run_at,
        timezone="UTC",
    )
    await scheduler.create_automation(
        title="自动触发",
        prompt="自动执行",
        conversation_id=conversation.id,
        schedule=schedule,
        next_run_at=run_at,
    )
    await scheduler.start()

    # 不手动调用 _trigger —— 事件循环正常运行，APScheduler 应自动触发。
    await asyncio.sleep(0.3)
    try:
        assert len(manager.started) >= 1
        completed = await store.list(status=AutomationStatus.COMPLETED)
        assert any(item.title == "自动触发" for item in completed)
    finally:
        await scheduler.shutdown()


# ---------------------------------------------------------------------------
# 19. Run 创建后、完成前崩溃：Automation 已持久化 last_run_id
# ---------------------------------------------------------------------------


async def test_last_run_id_persisted_before_crash(tmp_path) -> None:
    store = SQLiteAutomationStore(tmp_path / "vesta.db")
    await store.initialize()
    schedule = Schedule(
        kind=ScheduleKind.ONCE,
        run_at=_future(hours=1),
        timezone="UTC",
    )
    automation = await store.create(
        title="崩溃任务",
        prompt="执行",
        conversation_id="conv-1",
        schedule=schedule,
        next_run_at=schedule.run_at,
    )

    crash_service = CrashAfterStartService()
    scheduler = AutomationScheduler(store, crash_service)
    try:
        # dispatch 内部：Run 创建后 on_run_started 已持久化 last_run_id，随后崩溃。
        await scheduler._job_func(automation.id)()
    finally:
        await scheduler.shutdown()

    updated = await store.get(automation.id)
    assert updated is not None
    assert updated.last_run_id == "run-crash"
    assert updated.last_run_at is not None

    # 重启：不会把已启动的一次性再当作"从未执行"补跑 → COMPLETED。
    restarted_service = FakeConversationService()
    restarted = AutomationScheduler(store, restarted_service)
    try:
        await restarted._restore(await store.get(automation.id))
        after = await store.get(automation.id)
        assert after is not None
        assert after.status is AutomationStatus.COMPLETED
        assert restarted_service.dispatched == []
    finally:
        await restarted.shutdown()


# ---------------------------------------------------------------------------
# 20. 重启不会把已启动的一次性 Automation 再补跑（last_run_id 已存在）
# ---------------------------------------------------------------------------


async def test_restart_does_not_rerun_started_once_automation(tmp_path) -> None:
    store = SQLiteAutomationStore(tmp_path / "vesta.db")
    await store.initialize()
    schedule = Schedule(
        kind=ScheduleKind.ONCE,
        run_at=_future(hours=1),
        timezone="UTC",
    )
    automation = await store.create(
        title="已启动的一次性",
        prompt="执行",
        conversation_id="conv-1",
        schedule=schedule,
        next_run_at=_past(hours=1),
    )
    # 模拟崩溃前已通过 on_run_started 持久化 last_run_id（next 被清空）。
    await store.mark_triggered(
        automation.id,
        last_run_id="run-crash",
        last_run_at=datetime.now(UTC),
        next_run_at=None,
    )

    service = FakeConversationService()
    scheduler = AutomationScheduler(store, service)
    try:
        await scheduler._restore(await store.get(automation.id))
        after = await store.get(automation.id)
        assert after is not None
        assert after.status is AutomationStatus.COMPLETED
        # 没有补跑任何 dispatch。
        assert service.dispatched == []
    finally:
        await scheduler.shutdown()
