"""run RPC methods。

run.recover 保持现有语义：旧 Run 保持 INTERRUPTED，创建新 Run，
``new_run.recovered_from_run_id = old_run.id``。不修改 Checkpoint recovery 协议。
"""

from __future__ import annotations

from typing import Any

from app.agent.events import CompositeEventHandler
from app.run import RunStatus
from app.trace import SQLiteTraceEventHandler

from ..dispatcher import RpcContext, RpcDispatcher
from ..protocol import (
    INVALID_STATE,
    RESOURCE_NOT_FOUND,
    JsonRpcError,
    RpcErrorCode,
)


async def run_list(params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
    conversation_id = params.get("conversation_id")
    if conversation_id is not None and not isinstance(conversation_id, str):
        raise JsonRpcError(
            RpcErrorCode.INVALID_PARAMS,
            "conversation_id must be a string",
        )
    status = params.get("status")
    if status is not None:
        try:
            status = RunStatus(status)
        except ValueError as exc:
            raise JsonRpcError(
                RpcErrorCode.INVALID_PARAMS,
                f"invalid status: {status}",
            ) from exc
    limit = _positive_int(params, "limit", default=50)
    runs = await ctx.application.run_manager.list_runs(
        conversation_id=conversation_id,
        status=status,
        limit=limit,
    )
    return {"runs": runs, "count": len(runs)}


async def run_get(params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
    run_id = _require_str(params, "run_id")
    run = await ctx.application.run_manager.get_run(run_id)
    if run is None:
        raise JsonRpcError(RESOURCE_NOT_FOUND, "run not found")
    return {"run": run}


async def run_cancel(params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
    run_id = _require_str(params, "run_id")
    application = ctx.application
    run = await application.run_manager.get_run(run_id)
    if run is None:
        raise JsonRpcError(RESOURCE_NOT_FOUND, "run not found")
    try:
        updated = await application.run_manager.cancel(run.id)
    except ValueError as exc:
        raise JsonRpcError(INVALID_STATE, str(exc)) from exc
    # cancel 没有对应 AgentEvent，这里显式广播 run.status。
    await ctx.connection.hub.broadcast(
        "run.status",
        {"run_id": run_id, "status": updated.status.value},
    )
    return {"run": updated}


async def run_recover(params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
    run_id = _require_str(params, "run_id")
    application = ctx.application
    run = await application.run_manager.get_run(run_id)
    if run is None:
        raise JsonRpcError(RESOURCE_NOT_FOUND, "run not found")

    # 加载该会话最新 history / summary，与 CLI recover 路径一致。
    history: tuple[Any, ...] = ()
    summary_state = None
    if run.conversation_id is not None:
        history = tuple(
            await application.conversation_store.load_messages(run.conversation_id)
        )
        if application.summary_store is not None:
            summary_state = await application.summary_store.load(run.conversation_id)

    # Trace + 全局共享观察者（RPC 广播）都接上，恢复后的 Run 也能实时展示。
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
        raise JsonRpcError(INVALID_STATE, str(exc)) from exc
    recovered_run = await application.run_manager.wait(new_run_id)
    result = application.run_manager.result(new_run_id)
    return {
        "recovered_from_run_id": run.id,
        "run": recovered_run,
        "result": result,
    }


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
    dispatcher.register("run.list", run_list)
    dispatcher.register("run.get", run_get)
    dispatcher.register("run.cancel", run_cancel)
    dispatcher.register("run.recover", run_recover)
