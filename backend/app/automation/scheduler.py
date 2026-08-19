"""AutomationScheduler：到时间通过 RunManager 自动启动 Agent Run。

关键链路：

    Automation
        ↓ 到时间（APScheduler DateTrigger）
    AutomationScheduler
        ↓
    RunManager.start(prompt, conversation_id=..., history=..., summary_state=...)
        ↓
    AgentRuntime

Scheduler 绝不直接调用 AgentRuntime；所有 Agent 执行统一走 RunManager。

V1 调度模型：每个 ACTIVE Automation 注册一个"下次触发"的一次性
``DateTrigger`` job；每次触发后计算下一次触发时间并注册下一个一次性 job。
这样计划完全可控、与持久化 ``next_run_at`` 一致、重启恢复简单。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.context import SQLiteConversationSummaryStore
from app.conversation import SQLiteConversationStore

from .models import Automation, AutomationStatus, Schedule, ScheduleKind
from .store import SQLiteAutomationStore

logger = logging.getLogger("oneagent.automation.scheduler")

_JOB_PREFIX = "automation-"


class AutomationScheduler:
    """Automation 生命周期与到点触发（同时充当 Agent 工具的领域门面）。"""

    def __init__(
        self,
        store: SQLiteAutomationStore,
        run_manager: Any,
        *,
        conversation_store: SQLiteConversationStore | None = None,
        summary_store: SQLiteConversationSummaryStore | None = None,
        timezone: str = "UTC",
    ) -> None:
        self._store = store
        self._run_manager = run_manager  # duck-typed：start / get_run
        self._conversation_store = conversation_store
        self._summary_store = summary_store
        self._timezone = ZoneInfo(timezone)
        self._scheduler = AsyncIOScheduler(timezone=self._timezone)
        self._job_ids: dict[str, str] = {}
        self._running: set[str] = set()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """建表并恢复所有 ACTIVE Automation（应用 misfire 规则）后启动调度器。"""

        await self._store.initialize()
        active = await self._store.list(status=AutomationStatus.ACTIVE)
        for automation in active:
            await self._restore(automation)
        self._scheduler.start()

    async def shutdown(self) -> None:
        """优雅关闭：不阻塞、不遗留后台 asyncio task。"""

        try:
            self._scheduler.shutdown(wait=False)
        except Exception:  # noqa: BLE001
            logger.debug("automation scheduler already shut down", exc_info=True)
        self._running.clear()
        self._job_ids.clear()

    # ------------------------------------------------------------------
    # 领域操作（供 Agent 工具 / CLI 使用）
    # ------------------------------------------------------------------

    async def create_automation(
        self,
        *,
        title: str,
        prompt: str,
        conversation_id: str | None,
        schedule: Schedule,
        next_run_at: datetime,
    ) -> Automation:
        automation = await self._store.create(
            title=title,
            prompt=prompt,
            conversation_id=conversation_id,
            schedule=schedule,
            next_run_at=next_run_at,
        )
        self._schedule(automation, automation.next_run_at)
        return automation

    async def get(self, automation_id: str) -> Automation | None:
        return await self._store.get(automation_id)

    async def resolve(self, identifier: str) -> Automation | None:
        """按完整 ID 或唯一前缀查找。"""

        return await self._store.resolve(identifier)

    async def list(
        self,
        *,
        status: AutomationStatus | str | None = None,
        conversation_id: str | None = None,
        limit: int = 50,
    ) -> tuple[Automation, ...]:
        return await self._store.list(
            status=status,
            conversation_id=conversation_id,
            limit=limit,
        )

    async def cancel(self, automation_id: str) -> Automation:
        automation = await self._store.require(automation_id)
        if automation.status is AutomationStatus.CANCELLED:
            return automation
        updated = await self._store.update_status(
            automation_id,
            AutomationStatus.CANCELLED,
        )
        self._remove_job(automation_id)
        return updated

    async def pause(self, automation_id: str) -> Automation:
        automation = await self._store.require(automation_id)
        if automation.status is not AutomationStatus.ACTIVE:
            raise ValueError(
                f"only active automation can be paused: {automation_id} "
                f"({automation.status.value})"
            )
        updated = await self._store.update_status(
            automation_id,
            AutomationStatus.PAUSED,
        )
        self._remove_job(automation_id)
        return updated

    async def resume(self, automation_id: str) -> Automation:
        automation = await self._store.require(automation_id)
        if automation.status is not AutomationStatus.PAUSED:
            raise ValueError(
                f"only paused automation can be resumed: {automation_id} "
                f"({automation.status.value})"
            )
        now = datetime.now(UTC)
        if automation.schedule.kind is ScheduleKind.ONCE:
            if automation.last_run_id is not None:
                # 一次性已执行过 → 恢复后直接完成，不再触发。
                return await self._store.update_status(
                    automation_id,
                    AutomationStatus.COMPLETED,
                )
            next_run = automation.next_run_at or now
            if next_run < now:
                next_run = now  # 过期未执行 → resume 即补跑一次
        else:
            next_run = self._next_future(automation, now)
        updated = await self._store.update_status(
            automation_id,
            AutomationStatus.ACTIVE,
            next_run_at=next_run,
        )
        self._schedule(updated, next_run)
        return updated

    # ------------------------------------------------------------------
    # 内部：恢复 / 调度
    # ------------------------------------------------------------------

    async def _restore(self, automation: Automation) -> None:
        """启动恢复：应用 misfire 规则后把 Automation 重新接入调度。"""

        now = datetime.now(UTC)
        if automation.schedule.kind is ScheduleKind.ONCE:
            expired = (
                automation.next_run_at is None
                or automation.next_run_at <= now
            )
            if not expired:
                self._schedule(automation, automation.next_run_at)
                return
            if automation.last_run_id is None:
                # 程序关闭期间错过的一次性任务：只补执行一次。
                refreshed = await self._store.update_status(
                    automation.id,
                    AutomationStatus.ACTIVE,
                    next_run_at=now,
                )
                self._schedule(refreshed, now)
            else:
                # 已执行过 → 直接完成，不重复触发。
                await self._store.update_status(
                    automation.id,
                    AutomationStatus.COMPLETED,
                )
            return

        # 重复任务：misfire 恢复 —— 不补跑所有错过次数，从未来最近触发点继续。
        next_run = self._next_future(automation, now)
        if automation.next_run_at is None or next_run != automation.next_run_at:
            refreshed = await self._store.update_status(
                automation.id,
                AutomationStatus.ACTIVE,
                next_run_at=next_run,
            )
            automation = refreshed
        self._schedule(automation, next_run)

    def _schedule(self, automation: Automation, run_at: datetime) -> None:
        """注册（或重排）下一个一次性触发 job。"""

        job_id = _JOB_PREFIX + automation.id
        if job_id in self._job_ids:
            try:
                self._scheduler.reschedule_job(
                    job_id,
                    trigger=DateTrigger(run_at),
                )
                return
            except Exception:  # noqa: BLE001
                self._job_ids.pop(job_id, None)
        job = self._scheduler.add_job(
            self._job_func(automation.id),
            trigger=DateTrigger(run_at),
            id=job_id,
            replace_existing=True,
        )
        self._job_ids[automation.id] = job.id

    def _remove_job(self, automation_id: str) -> None:
        job_id = self._job_ids.pop(automation_id, None)
        if job_id is None:
            return
        try:
            self._scheduler.remove_job(job_id)
        except Exception:  # noqa: BLE001
            pass

    def _job_func(self, automation_id: str) -> Any:
        async def _run() -> None:
            try:
                await self._trigger(automation_id)
            except Exception:  # noqa: BLE001
                # 单个 Automation 失败不能让整个 Scheduler 崩溃。
                logger.exception(
                    "automation trigger failed: %s",
                    automation_id,
                )

        return _run

    # ------------------------------------------------------------------
    # 触发
    # ------------------------------------------------------------------

    async def _trigger(self, automation_id: str) -> None:
        """到点触发：通过 RunManager 启动一个 Agent Run 并更新执行元数据。

        max_instances = 1：若该 Automation 上一次 Run 仍在执行，跳过本次触发
        （coalesce —— 直接把 next_run_at 推进到下一个未来触发点，不新建 Run）。
        """

        if automation_id in self._running:
            return
        automation = await self._store.get(automation_id)
        if automation is None or automation.status is not AutomationStatus.ACTIVE:
            return

        now = datetime.now(UTC)
        if automation.last_run_id is not None:
            last_run = await self._run_manager.get_run(automation.last_run_id)
            if last_run is not None and last_run.status.value == "running":
                # 上一次 Run 还在执行 → 跳过本次，推进到下一个未来点。
                next_run = self._next_future(automation, now)
                await self._store.mark_triggered(
                    automation_id,
                    last_run_id=automation.last_run_id,
                    last_run_at=automation.last_run_at or now,
                    next_run_at=next_run,
                )
                self._schedule(automation, next_run)
                return

        self._running.add(automation_id)
        try:
            run_id, _ = await self._run_manager.start(
                automation.prompt,
                conversation_id=automation.conversation_id,
                history=await self._load_history(automation.conversation_id),
                summary_state=await self._load_summary(automation.conversation_id),
            )
            now = datetime.now(UTC)
            if automation.schedule.kind is ScheduleKind.ONCE:
                # 一次性任务：完成本次触发后进入 COMPLETED（保留 last_run_id
                # 供查看 Run 结果，Run 失败与否由 RunManager 负责）。
                await self._store.mark_triggered(
                    automation_id,
                    last_run_id=run_id,
                    last_run_at=now,
                    next_run_at=None,
                )
                await self._store.update_status(
                    automation_id,
                    AutomationStatus.COMPLETED,
                )
                self._remove_job(automation_id)
                return

            # 重复任务：仍 ACTIVE，计算下一次触发点。
            next_run = self._next_future(automation, now)
            await self._store.mark_triggered(
                automation_id,
                last_run_id=run_id,
                last_run_at=now,
                next_run_at=next_run,
            )
            self._schedule(automation, next_run)
        finally:
            self._running.discard(automation_id)

    # ------------------------------------------------------------------
    # 时间计算
    # ------------------------------------------------------------------

    def _build_trigger(self, schedule: Schedule):
        tz = ZoneInfo(schedule.timezone)
        if schedule.kind is ScheduleKind.INTERVAL:
            return IntervalTrigger(
                seconds=schedule.interval_seconds,
                timezone=tz,
            )
        if schedule.kind is ScheduleKind.CRON:
            return CronTrigger.from_crontab(schedule.cron_expr, timezone=tz)
        return DateTrigger(schedule.run_at)

    def _next_future(self, automation: Automation, now: datetime) -> datetime:
        """计算下一次（未来）触发时间；错过点不会被批量补跑。

        用 APScheduler trigger 的 ``get_next_fire_time`` 计算，从 now 之后取
        第一个未来触发点 —— 天然满足"重复任务不补跑所有错过次数"。
        """

        trigger = self._build_trigger(automation.schedule)
        local_now = now.astimezone(ZoneInfo(automation.schedule.timezone))
        next_local = trigger.get_next_fire_time(None, local_now)
        if next_local is None:
            raise ValueError(
                f"automation {automation.id} has no future trigger time"
            )
        return next_local.astimezone(UTC)

    # ------------------------------------------------------------------
    # Conversation 上下文（从持久化源加载，不依赖 CLI 内存 history）
    # ------------------------------------------------------------------

    async def _load_history(self, conversation_id: str | None) -> tuple[Any, ...]:
        if self._conversation_store is None or not conversation_id:
            return ()
        try:
            return tuple(
                await self._conversation_store.load_messages(conversation_id)
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "failed to load conversation history for automation",
            )
            return ()

    async def _load_summary(self, conversation_id: str | None) -> Any:
        if self._summary_store is None or not conversation_id:
            return None
        try:
            return await self._summary_store.load(conversation_id)
        except Exception:  # noqa: BLE001
            logger.exception("failed to load conversation summary for automation")
            return None


__all__ = ["AutomationScheduler"]
