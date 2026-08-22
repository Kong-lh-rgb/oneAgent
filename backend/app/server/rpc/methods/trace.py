"""trace RPC methods：直接读现有 TraceStore，不另建第二套日志模型。"""

from __future__ import annotations

from typing import Any

from app.trace import summarize_run_usage

from ..dispatcher import RpcContext, RpcDispatcher
from ..protocol import RESOURCE_NOT_FOUND, JsonRpcError, RpcErrorCode


async def trace_get(params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
    run_id = params.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise JsonRpcError(RpcErrorCode.INVALID_PARAMS, "run_id is required")
    application = ctx.application
    trace = await application.trace_store.get(run_id)
    if trace is None:
        raise JsonRpcError(RESOURCE_NOT_FOUND, "run trace not found")
    events = await application.trace_store.load_events(run_id)
    return {
        "run": trace,
        "events": events,
        "usage": summarize_run_usage(events),
    }


def register(dispatcher: RpcDispatcher) -> None:
    dispatcher.register("trace.get", trace_get)
