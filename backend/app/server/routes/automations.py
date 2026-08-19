"""Automation API：列表 / 详情 / 创建 / pause / resume / cancel。

创建接口接收结构化 schedule（once / interval / cron），复用
``app.automation.tools.build_schedule_and_next`` 做参数校验与 next_run_at
计算，不做自然语言时间解析。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.automation.tools import build_schedule_and_next

from ..schemas import CreateAutomationRequest

router = APIRouter(tags=["automations"])


@router.get("/automations")
async def list_automations(
    request: Request,
    conversation_id: str | None = None,
    limit: int = 50,
) -> dict[str, object]:
    application = request.app.state.application
    automations = await application.automation_scheduler.list(
        conversation_id=conversation_id,
        limit=limit,
    )
    return {"automations": automations, "count": len(automations)}


@router.get("/automations/{automation_id}")
async def get_automation(
    automation_id: str,
    request: Request,
) -> dict[str, object]:
    application = request.app.state.application
    automation = await application.automation_scheduler.get(automation_id)
    if automation is None:
        raise HTTPException(status_code=404, detail="automation not found")
    return {"automation": automation}


@router.post("/automations")
async def create_automation(
    body: CreateAutomationRequest,
    request: Request,
) -> dict[str, object]:
    application = request.app.state.application
    try:
        schedule, next_run_at = build_schedule_and_next(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    automation = await application.automation_scheduler.create_automation(
        title=body.title,
        prompt=body.prompt,
        conversation_id=body.conversation_id,
        schedule=schedule,
        next_run_at=next_run_at,
    )
    return {"automation": automation}


@router.post("/automations/{automation_id}/pause")
async def pause_automation(
    automation_id: str,
    request: Request,
) -> dict[str, object]:
    application = request.app.state.application
    automation = await application.automation_scheduler.get(automation_id)
    if automation is None:
        raise HTTPException(status_code=404, detail="automation not found")
    try:
        updated = await application.automation_scheduler.pause(automation.id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"automation": updated}


@router.post("/automations/{automation_id}/resume")
async def resume_automation(
    automation_id: str,
    request: Request,
) -> dict[str, object]:
    application = request.app.state.application
    automation = await application.automation_scheduler.get(automation_id)
    if automation is None:
        raise HTTPException(status_code=404, detail="automation not found")
    try:
        updated = await application.automation_scheduler.resume(automation.id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"automation": updated}


@router.post("/automations/{automation_id}/cancel")
async def cancel_automation(
    automation_id: str,
    request: Request,
) -> dict[str, object]:
    application = request.app.state.application
    automation = await application.automation_scheduler.get(automation_id)
    if automation is None:
        raise HTTPException(status_code=404, detail="automation not found")
    updated = await application.automation_scheduler.cancel(automation.id)
    return {"automation": updated}


__all__ = ["router"]
