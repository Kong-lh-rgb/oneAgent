"""Automation Agent 工具：让模型通过自然语言创建 / 管理自动化。

模型负责把“明天上午九点提醒我交作业”转换成结构化参数；工具只接收明确
结构化时间，不做自然语言解析。时间格式 / timezone / 过去时间 / interval /
recurrence 合法性全部在工具层校验。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger

from app.models.types import ToolDefinition

from ..tools.base import BaseTool
from ..tools.hooks import ToolExecutionContext
from ..tools.registry import ToolRegistry
from .models import Schedule, ScheduleKind
from .scheduler import AutomationScheduler

_KIND_NAMES = {kind.value for kind in ScheduleKind}


class AutomationCreateTool(BaseTool):
    """创建一个未来自动触发的 Agent Run。"""

    def __init__(self, scheduler: AutomationScheduler) -> None:
        self._scheduler = scheduler

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="automation_create",
            description=(
                "创建一个自动化：在未来某个时间，自动用指定 prompt 启动一次 "
                "Agent Run（可选一次性、固定间隔、cron 计划）。当用户要求"
                "“明天早上9点提醒我 / 每天晚上10点总结进度 / 每隔2小时检查”"
                "这类定时任务时调用。\n"
                "注意：prompt 只保存“到触发时间真正要执行的指令”，不能包含"
                "调度条件（时间、频率、时区等必须放进 kind / run_at / "
                "interval_seconds / cron_expr / timezone，不要在 prompt 里重复）。"
                "例：“每天晚上10点总结项目进度” → schedule=每天22:00，"
                "prompt=“总结项目进度”。否则自动化触发后模型会把整个句子"
                "当指令执行，可能再次创建新的自动化。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "自动化标题，简短描述用途。",
                    },
                    "prompt": {
                        "type": "string",
                        "description": (
                            "触发时真正要执行的指令，只含执行内容、不含调度条件。"
                            "例：“每天晚上10点总结项目进度”应拆为 schedule="
                            "每天22:00、prompt=“总结项目进度”；不要在 prompt 里"
                            "重复时间/频率，否则自动化触发后模型可能再次创建"
                            "新的自动化。"
                        ),
                    },
                    "kind": {
                        "type": "string",
                        "enum": [kind.value for kind in ScheduleKind],
                        "description": (
                            "计划类型：once=一次性；interval=固定间隔；"
                            "cron=crontab 表达式。"
                        ),
                    },
                    "run_at": {
                        "type": "string",
                        "description": (
                            "一次性时间，必须带时区偏移的 ISO8601，如 "
                            "2026-08-20T09:00:00+08:00。仅 kind=once 使用。"
                        ),
                    },
                    "interval_seconds": {
                        "type": "number",
                        "description": (
                            "固定间隔（秒），必须大于 0。仅 kind=interval 使用。"
                        ),
                    },
                    "cron_expr": {
                        "type": "string",
                        "description": (
                            "crontab 五段表达式，如 \"0 9 * * *\"（每天09:00）、"
                            "\"0 10 * * 1\"（每周一10:00）。仅 kind=cron 使用。"
                        ),
                    },
                    "timezone": {
                        "type": "string",
                        "description": (
                            "IANA 时区名，如 Asia/Shanghai。默认 UTC。"
                        ),
                    },
                },
                "required": ["title", "prompt", "kind"],
                "additionalProperties": False,
            },
            strict=True,
        )

    async def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        raise ValueError("automation_create requires conversation context")

    async def execute_with_context(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        conversation_id = context.conversation_id
        title = _require_non_empty(arguments, "title")
        prompt = _require_non_empty(arguments, "prompt")
        schedule, next_run_at = build_schedule_and_next(arguments)

        automation = await self._scheduler.create_automation(
            title=title,
            prompt=prompt,
            conversation_id=conversation_id,
            schedule=schedule,
            next_run_at=next_run_at,
        )
        return _automation_brief(automation)


class AutomationListTool(BaseTool):
    """列出自动化（当前会话创建的）。"""

    def __init__(self, scheduler: AutomationScheduler) -> None:
        self._scheduler = scheduler

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="automation_list",
            description="列出当前会话创建的全部自动化及其状态。",
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            strict=True,
        )

    async def execute_with_context(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        automations = await self._scheduler.list(
            conversation_id=context.conversation_id,
        )
        return {"automations": [_automation_brief(item) for item in automations]}


class AutomationGetTool(BaseTool):
    """查看单个自动化详情。"""

    def __init__(self, scheduler: AutomationScheduler) -> None:
        self._scheduler = scheduler

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="automation_get",
            description="按 ID 查看一条自动化的完整详情。",
            parameters={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "自动化 ID。"},
                },
                "required": ["id"],
                "additionalProperties": False,
            },
            strict=True,
        )

    async def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        automation_id = _require_non_empty(arguments, "id")
        automation = await self._scheduler.get(automation_id)
        if automation is None:
            raise KeyError(f"自动化不存在：{automation_id}")
        return _automation_full(automation)


class AutomationCancelTool(BaseTool):
    """取消一个自动化（不再触发）。"""

    def __init__(self, scheduler: AutomationScheduler) -> None:
        self._scheduler = scheduler

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="automation_cancel",
            description="取消一条自动化：状态变为 cancelled，不再触发。",
            parameters={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "自动化 ID。"},
                },
                "required": ["id"],
                "additionalProperties": False,
            },
            strict=True,
        )

    async def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        automation_id = _require_non_empty(arguments, "id")
        automation = await self._scheduler.cancel(automation_id)
        return _automation_brief(automation)


class AutomationPauseTool(BaseTool):
    """暂停一个自动化（保留，但不触发）。"""

    def __init__(self, scheduler: AutomationScheduler) -> None:
        self._scheduler = scheduler

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="automation_pause",
            description="暂停一条自动化：状态变为 paused，暂时不再触发。",
            parameters={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "自动化 ID。"},
                },
                "required": ["id"],
                "additionalProperties": False,
            },
            strict=True,
        )

    async def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        automation_id = _require_non_empty(arguments, "id")
        automation = await self._scheduler.pause(automation_id)
        return _automation_brief(automation)


class AutomationResumeTool(BaseTool):
    """恢复一个已暂停的自动化。"""

    def __init__(self, scheduler: AutomationScheduler) -> None:
        self._scheduler = scheduler

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="automation_resume",
            description="恢复一个已暂停的自动化：重新按计划触发。",
            parameters={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "自动化 ID。"},
                },
                "required": ["id"],
                "additionalProperties": False,
            },
            strict=True,
        )

    async def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        automation_id = _require_non_empty(arguments, "id")
        automation = await self._scheduler.resume(automation_id)
        return _automation_brief(automation)


def register_automation_tools(
    registry: ToolRegistry,
    scheduler: AutomationScheduler,
) -> None:
    registry.register(AutomationCreateTool(scheduler))
    registry.register(AutomationListTool(scheduler))
    registry.register(AutomationGetTool(scheduler))
    registry.register(AutomationCancelTool(scheduler))
    registry.register(AutomationPauseTool(scheduler))
    registry.register(AutomationResumeTool(scheduler))


# ---------------------------------------------------------------------------
# 校验与构造
# ---------------------------------------------------------------------------


def build_schedule_and_next(
    arguments: dict[str, Any],
) -> tuple[Schedule, datetime]:
    """从工具参数构造 Schedule，并计算初始 next_run_at。

    校验：kind / timezone / run_at 格式与未来时间 / interval>0 / cron 合法性。
    不做自然语言时间解析 —— 模型负责把口语时间转成结构化参数。
    """

    raw_kind = arguments.get("kind")
    if not isinstance(raw_kind, str) or raw_kind not in _KIND_NAMES:
        raise ValueError(
            f"'kind' must be one of: {', '.join(sorted(_KIND_NAMES))}"
        )
    kind = ScheduleKind(raw_kind)
    timezone = arguments.get("timezone") or "UTC"
    if not isinstance(timezone, str):
        raise ValueError("'timezone' must be a string")
    try:
        tz = ZoneInfo(timezone)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"invalid timezone: {timezone}") from exc

    now = datetime.now(UTC)

    if kind is ScheduleKind.ONCE:
        raw_run_at = arguments.get("run_at")
        if not isinstance(raw_run_at, str) or not raw_run_at.strip():
            raise ValueError("'run_at' is required for kind=once")
        try:
            run_at = datetime.fromisoformat(raw_run_at)
        except ValueError as exc:
            raise ValueError(f"invalid run_at: {raw_run_at!r}") from exc
        if run_at.tzinfo is None or run_at.utcoffset() is None:
            raise ValueError(
                "'run_at' must include a timezone offset "
                "(e.g. 2026-08-20T09:00:00+08:00)"
            )
        run_at = run_at.astimezone(UTC)
        if run_at <= now:
            raise ValueError("'run_at' must be in the future")
        schedule = Schedule(
            kind=kind,
            run_at=run_at,
            timezone=timezone,
        )
        return schedule, run_at

    if kind is ScheduleKind.INTERVAL:
        raw_interval = arguments.get("interval_seconds")
        if not isinstance(raw_interval, (int, float)):
            raise ValueError("'interval_seconds' is required for kind=interval")
        interval = float(raw_interval)
        if interval <= 0:
            raise ValueError("'interval_seconds' must be > 0")
        schedule = Schedule(
            kind=kind,
            interval_seconds=interval,
            timezone=timezone,
        )
        return schedule, now + timedelta(seconds=interval)

    # CRON
    raw_cron = arguments.get("cron_expr")
    if not isinstance(raw_cron, str) or not raw_cron.strip():
        raise ValueError("'cron_expr' is required for kind=cron")
    try:
        trigger = CronTrigger.from_crontab(raw_cron, timezone=tz)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"invalid cron_expr: {raw_cron!r}") from exc
    next_local = trigger.get_next_fire_time(None, now.astimezone(tz))
    if next_local is None:
        raise ValueError(f"cron_expr has no future fire time: {raw_cron!r}")
    schedule = Schedule(
        kind=kind,
        cron_expr=raw_cron,
        timezone=timezone,
    )
    return schedule, next_local.astimezone(UTC)


def _require_non_empty(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{key}' must be a non-empty string")
    return value.strip()


def _automation_brief(automation) -> dict[str, Any]:
    return {
        "id": automation.id,
        "title": automation.title,
        "status": automation.status.value,
        "kind": automation.schedule.kind.value,
        "next_run_at": (
            automation.next_run_at.isoformat()
            if automation.next_run_at is not None
            else None
        ),
        "last_run_id": automation.last_run_id,
    }


def _automation_full(automation) -> dict[str, Any]:
    return json.loads(automation.model_dump_json())


__all__ = [
    "AutomationCancelTool",
    "AutomationCreateTool",
    "AutomationGetTool",
    "AutomationListTool",
    "AutomationPauseTool",
    "AutomationResumeTool",
    "build_schedule_and_next",
    "register_automation_tools",
]
