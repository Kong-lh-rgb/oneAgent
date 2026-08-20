"""automation RPC methods。

automation.create 继续使用现有 AutomationScheduler / Schedule / ScheduleKind，
只接受结构化 once / interval / cron，不做自然语言时间解析。
"""

from __future__ import annotations

from typing import Any

from app.automation.tools import build_schedule_and_next

from ..dispatcher import RpcContext, RpcDispatcher
from ..protocol import (
    INVALID_STATE,
    RESOURCE_NOT_FOUND,
    JsonRpcError,
    RpcErrorCode,
)


async def automation_list(
    params: dict[str, Any],
    ctx: RpcContext,
) -> dict[str, Any]:
    conversation_id = params.get("conversation_id")
    if conversation_id is not None and not isinstance(conversation_id, str):
        raise JsonRpcError(
            RpcErrorCode.INVALID_PARAMS,
            "conversation_id must be a string",
        )
    limit = _positive_int(params, "limit", default=50)
    automations = await ctx.application.automation_scheduler.list(
        conversation_id=conversation_id,
        limit=limit,
    )
    return {"automations": automations, "count": len(automations)}


async def automation_get(
    params: dict[str, Any],
    ctx: RpcContext,
) -> dict[str, Any]:
    automation_id = _require_str(params, "automation_id")
    automation = await ctx.application.automation_scheduler.get(automation_id)
    if automation is None:
        raise JsonRpcError(RESOURCE_NOT_FOUND, "automation not found")
    return {"automation": automation}


async def automation_create(
    params: dict[str, Any],
    ctx: RpcContext,
) -> dict[str, Any]:
    title = _require_str(params, "title")
    prompt = _require_str(params, "prompt")
    conversation_id = params.get("conversation_id")
    if conversation_id is not None and not isinstance(conversation_id, str):
        raise JsonRpcError(
            RpcErrorCode.INVALID_PARAMS,
            "conversation_id must be a string",
        )
    try:
        schedule, next_run_at = build_schedule_and_next(params)
    except ValueError as exc:
        raise JsonRpcError(RpcErrorCode.INVALID_PARAMS, str(exc)) from exc
    automation = await ctx.application.automation_scheduler.create_automation(
        title=title,
        prompt=prompt,
        conversation_id=conversation_id,
        schedule=schedule,
        next_run_at=next_run_at,
    )
    return {"automation": automation}


async def _control(
    params: dict[str, Any],
    ctx: RpcContext,
    action: str,
) -> dict[str, Any]:
    automation_id = _require_str(params, "automation_id")
    scheduler = ctx.application.automation_scheduler
    automation = await scheduler.get(automation_id)
    if automation is None:
        raise JsonRpcError(RESOURCE_NOT_FOUND, "automation not found")
    try:
        if action == "pause":
            updated = await scheduler.pause(automation.id)
        elif action == "resume":
            updated = await scheduler.resume(automation.id)
        else:
            updated = await scheduler.cancel(automation.id)
    except ValueError as exc:
        raise JsonRpcError(INVALID_STATE, str(exc)) from exc
    return {"automation": updated}


async def automation_pause(
    params: dict[str, Any],
    ctx: RpcContext,
) -> dict[str, Any]:
    return await _control(params, ctx, "pause")


async def automation_resume(
    params: dict[str, Any],
    ctx: RpcContext,
) -> dict[str, Any]:
    return await _control(params, ctx, "resume")


async def automation_cancel(
    params: dict[str, Any],
    ctx: RpcContext,
) -> dict[str, Any]:
    return await _control(params, ctx, "cancel")


def _require_str(params: dict[str, Any], key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value:
        raise JsonRpcError(RpcErrorCode.INVALID_PARAMS, f"{key} is required")
    return value


def _positive_int(params: dict[str, Any], key: str, *, default: int) -> int:
    value = params.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise JsonRpcError(
            RpcErrorCode.INVALID_PARAMS,
            f"{key} must be a positive integer",
        )
    return value


def register(dispatcher: RpcDispatcher) -> None:
    dispatcher.register("automation.list", automation_list)
    dispatcher.register("automation.get", automation_get)
    dispatcher.register("automation.create", automation_create)
    dispatcher.register("automation.pause", automation_pause)
    dispatcher.register("automation.resume", automation_resume)
    dispatcher.register("automation.cancel", automation_cancel)
