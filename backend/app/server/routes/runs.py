"""Run API：列表 / 详情 / 取消 / 恢复 / Trace。

recover 直接走现有 ``RunManager.recover`` 语义：旧 Run 保持 INTERRUPTED，
创建新 Run，``new_run.recovered_from_run_id = old_run.id``。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.agent.events import CompositeEventHandler
from app.run import RunStatus
from app.trace import SQLiteTraceEventHandler

router = APIRouter(tags=["runs"])


@router.get("/runs")
async def list_runs(
    request: Request,
    conversation_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> dict[str, object]:
    application = request.app.state.application
    runs = await application.run_manager.list_runs(
        conversation_id=conversation_id,
        status=_parse_status(status),
        limit=limit,
    )
    return {"runs": runs, "count": len(runs)}


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request) -> dict[str, object]:
    application = request.app.state.application
    run = await application.run_manager.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return {"run": run}


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, request: Request) -> dict[str, object]:
    application = request.app.state.application
    run = await application.run_manager.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    try:
        updated = await application.run_manager.cancel(run.id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    broker = request.app.state.broker
    await broker.publish_run_status(run_id, updated.status.value)
    return {"run": updated}


@router.post("/runs/{run_id}/recover")
async def recover_run(run_id: str, request: Request) -> dict[str, object]:
    application = request.app.state.application
    run = await application.run_manager.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")

    # 加载该会话最新 history / summary，与 CLI recover 路径一致。
    history: tuple[object, ...] = ()
    summary_state = None
    if run.conversation_id is not None:
        history = tuple(
            await application.conversation_store.load_messages(run.conversation_id)
        )
        if application.summary_store is not None:
            summary_state = await application.summary_store.load(run.conversation_id)

    # Trace + 全局共享观察者（Desktop 广播）都接上，恢复后的 Run 也能实时展示。
    handlers = [SQLiteTraceEventHandler(application.trace_store)]
    if application.shared_event_handler is not None:
        handlers.append(application.shared_event_handler)
    event_handler = (
        CompositeEventHandler(*handlers) if len(handlers) > 1 else handlers[0]
    )

    try:
        new_run_id, _task = await application.run_manager.recover(
            run.id,
            history=history,
            summary_state=summary_state,
            event_handler=event_handler,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    recovered_run = await application.run_manager.wait(new_run_id)
    result = application.run_manager.result(new_run_id)
    return {
        "recovered_from_run_id": run.id,
        "run": recovered_run,
        "result": result,
    }


@router.get("/runs/{run_id}/trace")
async def get_run_trace(run_id: str, request: Request) -> dict[str, object]:
    application = request.app.state.application
    trace = await application.trace_store.get(run_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="run trace not found")
    events = await application.trace_store.load_events(run_id)
    return {"run": trace, "events": events}


def _parse_status(value: str | None) -> RunStatus | None:
    if value is None or not value.strip():
        return None
    try:
        return RunStatus(value.strip())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


__all__ = ["router"]
