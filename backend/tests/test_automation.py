"""Automation / Scheduler V1 测试。

覆盖：一次性 / 重复任务触发、状态管理、重启恢复、misfire/coalesce、
并发保护、工具校验、持久化 Conversation 上下文加载。

全部使用 fake RunManager / fake ConversationStore，不调用真实模型 API；
用可控时间（直接构造过去/未来 next_run_at）避免真实等待。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.automation.models import (
    AutomationStatus,
    Schedule,
    ScheduleKind,
)
from app.automation.scheduler import AutomationScheduler
from app.automation.store import SQLiteAutomationStore
from app.automation.tools import build_schedule_and_next
from app.run import RunStatus

_USER_MESSAGE = "提醒我交作业"


class FakeRun:
    def __init__(self, status: RunStatus) -> None:
        self.status = status


class FakeRunManager:
    """记录 start 调用；可配置上一次 Run 是否仍在执行。"""

    def __init__(self) -> None:
        self.started: list[tuple[str, dict]] = []
        self.runs: dict[str, FakeRun] = {}
        self.fail_on_start: bool = False

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
        if self.fail_on_start:
            raise RuntimeError("run failed to start")
        run_id = f"run-{len(self.started) + 1}"
        self.started.append(
            (
                run_id,
                {
                    "prompt": user_message,
                    "conversation_id": conversation_id,
                    "history": history,
                    "summary_state": summary_state,
                },
            )
        )
        self.runs[run_id] = FakeRun(RunStatus.RUNNING)
        return run_id, None

    async def get_run(self, run_id: str) -> FakeRun | None:
        return self.runs.get(run_id)

    def finish_run(self, run_id: str, status: RunStatus) -> None:
        self.runs[run_id] = FakeRun(status)


class FakeConversationStore:
    def __init__(self, messages=()) -> None:
        self.messages = messages

    async def load_messages(self, conversation_id: str):
        if conversation_id == "missing":
            raise KeyError("会话不存在")
        return self.messages


class FakeSummaryStore:
    def __init__(self, state=None) -> None:
        self.state = state

    async def load(self, conversation_id: str):
        return self.state


@pytest.fixture
async def make_scheduler(tmp_path):
    """构造 (store, scheduler)；测试结束后统一 shutdown。"""

    schedulers: list[AutomationScheduler] = []

    async def _make(
        run_manager: FakeRunManager | None = None,
        conversation_store: FakeConversationStore | None = None,
        summary_store: FakeSummaryStore | None = None,
    ) -> tuple[SQLiteAutomationStore, AutomationScheduler, FakeRunManager]:
        store = SQLiteAutomationStore(tmp_path / "oneagent.db")
        await store.initialize()
        manager = run_manager or FakeRunManager()
        scheduler = AutomationScheduler(
            store,
            manager,
            conversation_store=conversation_store,
            summary_store=summary_store,
        )
        schedulers.append(scheduler)
        return store, scheduler, manager

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
# 1. 创建一次性 Automation + 2/3. 到点触发 RunManager.start + conversation 关联
# ---------------------------------------------------------------------------


async def test_create_once_automation_and_trigger_start(make_scheduler) -> None:
    store, scheduler, manager = await make_scheduler(
        conversation_store=FakeConversationStore()
    )
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

    # 到点触发。
    await scheduler._trigger(automation.id)

    assert len(manager.started) == 1
    started = manager.started[0][1]
    assert started["prompt"] == _USER_MESSAGE
    assert started["conversation_id"] == "conv-1"


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
    # 不再触发。
    await scheduler._trigger(automation.id)
    assert len(scheduler._running) == 0


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
    store, scheduler, manager = await make_scheduler()
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

    # pause：不再触发。
    paused = await scheduler.pause(automation.id)
    assert paused.status is AutomationStatus.PAUSED
    await scheduler._trigger(automation.id)
    assert manager.started == []

    # resume：重新调度，触发生效。
    resumed = await scheduler.resume(automation.id)
    assert resumed.status is AutomationStatus.ACTIVE
    await scheduler._trigger(automation.id)
    assert len(manager.started) == 1

    # cancel：不再触发。
    cancelled = await scheduler.cancel(automation.id)
    assert cancelled.status is AutomationStatus.CANCELLED
    await scheduler._trigger(automation.id)
    assert len(manager.started) == 1
    # 终态不可 pause。
    with pytest.raises(ValueError, match="only active"):
        await scheduler.pause(automation.id)


# ---------------------------------------------------------------------------
# 10. 程序重启后 ACTIVE Automation 被重新加载
# ---------------------------------------------------------------------------


async def test_restart_reloads_active_automations(tmp_path) -> None:
    store = SQLiteAutomationStore(tmp_path / "oneagent.db")
    await store.initialize()
    manager = FakeRunManager()
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

    # “新进程”：新建 scheduler，start() 应加载 ACTIVE automation 并注册 job。
    restarted = AutomationScheduler(
        store,
        manager,
        conversation_store=FakeConversationStore(),
    )
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
    store = SQLiteAutomationStore(tmp_path / "oneagent.db")
    await store.initialize()
    manager = FakeRunManager()
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

    scheduler = AutomationScheduler(store, manager)
    try:
        # 应用 misfire 恢复规则（白盒调用，避免 APScheduler 真实触发造成竞态）。
        await scheduler._restore(automation)
        updated = await store.get(automation.id)
        assert updated is not None
        assert updated.status is AutomationStatus.ACTIVE
        assert updated.next_run_at is not None
        assert updated.next_run_at <= datetime.now(UTC) + timedelta(seconds=5)

        await scheduler._trigger(automation.id)
        assert len(manager.started) == 1
        completed = await store.get(automation.id)
        assert completed is not None
        assert completed.status is AutomationStatus.COMPLETED

        # 已完成后不再补跑。
        await scheduler._trigger(automation.id)
        assert len(manager.started) == 1
    finally:
        await scheduler.shutdown()


# ---------------------------------------------------------------------------
# 12. 重复任务 misfire 不批量补跑
# ---------------------------------------------------------------------------


async def test_recurring_misfire_does_not_batch_catchup(tmp_path) -> None:
    store = SQLiteAutomationStore(tmp_path / "oneagent.db")
    await store.initialize()
    manager = FakeRunManager()
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
        next_run_at=_past(hours=5),  # 关闭期间错过了 5 个触发点
    )

    scheduler = AutomationScheduler(store, manager)
    try:
        await scheduler.start()
        # 不应补跑历史点：next_run_at 直接跳到未来，job 只注册一个。
        updated = await store.get(automation.id)
        assert updated is not None
        assert updated.next_run_at is not None
        assert updated.next_run_at > datetime.now(UTC)
        assert len(manager.started) == 0
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
    store, scheduler, manager = await make_scheduler()
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

    # 第一次触发：Run 开始（仍在 RUNNING）。
    await scheduler._trigger(automation.id)
    assert len(manager.started) == 1
    refreshed = await store.get(automation.id)
    assert refreshed is not None and refreshed.last_run_id is not None
    assert manager.runs[refreshed.last_run_id].status is RunStatus.RUNNING

    # 下一次触发：上一次 Run 还在执行 → 跳过（不新建 Run）。
    await scheduler._trigger(automation.id)
    assert len(manager.started) == 1

    # 上一次 Run 结束后，下一次触发正常执行。
    manager.finish_run(refreshed.last_run_id, RunStatus.COMPLETED)
    await scheduler._trigger(automation.id)
    assert len(manager.started) == 2


# ---------------------------------------------------------------------------
# 14. 单个 Automation Run FAILED 不崩 Scheduler
# ---------------------------------------------------------------------------


async def test_run_failure_does_not_crash_scheduler(make_scheduler) -> None:
    store, scheduler, manager = await make_scheduler()
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

    # 触发时 Run 启动失败 → job 包装函数应隔离异常，不崩调度器。
    manager.fail_on_start = True
    await scheduler._job_func(automation.id)()
    manager.fail_on_start = False

    # Scheduler 仍可用。
    await scheduler._trigger(automation.id)
    assert len(manager.started) == 1


# ---------------------------------------------------------------------------
# 15. automation_create Tool 参数校验
# ---------------------------------------------------------------------------


def test_automation_create_argument_validation() -> None:
    # 非法 kind。
    with pytest.raises(ValueError, match="kind"):
        build_schedule_and_next({"kind": "hourly", "prompt": "x", "title": "t"})
    # once 缺 run_at。
    with pytest.raises(ValueError, match="run_at"):
        build_schedule_and_next({"kind": "once", "prompt": "x", "title": "t"})
    # run_at 无时区偏移。
    with pytest.raises(ValueError, match="timezone offset"):
        build_schedule_and_next(
            {
                "kind": "once",
                "run_at": "2026-08-20T09:00:00",
                "prompt": "x",
                "title": "t",
            }
        )
    # run_at 是过去时间。
    with pytest.raises(ValueError, match="future"):
        build_schedule_and_next(
            {
                "kind": "once",
                "run_at": "2020-01-01T00:00:00+08:00",
                "prompt": "x",
                "title": "t",
            }
        )
    # interval 非正。
    with pytest.raises(ValueError, match="interval_seconds"):
        build_schedule_and_next(
            {"kind": "interval", "interval_seconds": 0, "prompt": "x", "title": "t"}
        )
    with pytest.raises(ValueError, match="interval_seconds"):
        build_schedule_and_next(
            {"kind": "interval", "prompt": "x", "title": "t"}
        )
    # cron 表达式非法。
    with pytest.raises(ValueError, match="cron_expr"):
        build_schedule_and_next(
            {"kind": "cron", "cron_expr": "not a cron", "prompt": "x", "title": "t"}
        )
    # 非法 timezone。
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
    # once 带时区偏移（未来）。
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
    # interval。
    schedule, next_run = build_schedule_and_next(
        {"kind": "interval", "interval_seconds": 7200, "prompt": "x", "title": "t"}
    )
    assert schedule.kind is ScheduleKind.INTERVAL
    assert next_run > datetime.now(UTC)
    # cron（每天 09:00）。
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


# ---------------------------------------------------------------------------
# 16. 触发时从持久化 Conversation 加载上下文
# ---------------------------------------------------------------------------


async def test_trigger_loads_context_from_persistent_conversation(
    make_scheduler,
) -> None:
    messages = (
        {"role": "user", "content": "之前的上下文"},
    )
    state = object()
    store, scheduler, manager = await make_scheduler(
        conversation_store=FakeConversationStore(messages=messages),
        summary_store=FakeSummaryStore(state=state),
    )
    schedule = Schedule(
        kind=ScheduleKind.ONCE,
        run_at=_future(hours=1),
        timezone="UTC",
    )
    automation = await scheduler.create_automation(
        title="带上下文",
        prompt="继续",
        conversation_id="conv-9",
        schedule=schedule,
        next_run_at=schedule.run_at,
    )

    await scheduler._trigger(automation.id)

    assert len(manager.started) == 1
    started = manager.started[0][1]
    assert started["conversation_id"] == "conv-9"
    assert started["history"] == messages
    assert started["summary_state"] is state
