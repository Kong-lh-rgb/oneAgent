"""computer RPC methods：Host 状态 / 权限请求 / 只读最新 Observation。

- ``computer.status``：只读，不弹权限提示；helper 死了可安全 ensure_started。
- ``computer.request_permission``：仅 Desktop 用户显式点击时调用（prompt=true）。
- ``computer.latest_observation``：从 durable Trace 读取（不调用 runtime.observe，
  不抢 Machine Lease）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.agent.events import AgentEventType

from ..dispatcher import RpcContext, RpcDispatcher
from ..protocol import INVALID_STATE, JsonRpcError, RpcErrorCode

logger = logging.getLogger("oneagent.server.rpc.computer")

_PERMISSIONS = frozenset({"accessibility", "screen_recording"})


def _permission_state(value: bool | None) -> str:
    if value is None:
        return "unknown"
    return "granted" if value else "required"


def _lease_dict(application: Any) -> dict[str, Any] | None:
    lease = application.computer_lease
    if lease is None:
        return None
    snapshot = lease.snapshot
    if snapshot.owner_run_id is None:
        return None
    return {
        "busy": True,
        "owner_run_id": snapshot.owner_run_id,
        "acquired_at": (
            snapshot.acquired_at.isoformat()
            if snapshot.acquired_at is not None
            else None
        ),
        "process_id": snapshot.process_id,
    }


async def _live_permissions(application: Any) -> tuple[str, str]:
    """读取 helper 实时权限状态（不弹窗）；不可用时返回 unknown。"""

    runtime = application.computer_runtime
    if runtime is None:
        return "unknown", "unknown"
    client = getattr(runtime, "helper_client", None)
    if client is None:
        return "unknown", "unknown"
    try:
        await client.ensure_started()
    except Exception as exc:
        logger.warning("computer status: helper unavailable: %s", exc)
        return "unknown", "unknown"
    try:
        accessibility = await client.call("accessibility_status", {})
        screen = await client.call("screen_capture_status", {})
    except Exception as exc:
        logger.warning("computer status permission check failed: %s", exc)
        return "unknown", "unknown"
    return (
        _permission_state(accessibility.get("trusted")),
        _permission_state(screen.get("granted")),
    )


def _status_dict(
    application: Any,
    *,
    accessibility: str,
    screen: str,
    lease: dict[str, Any] | None,
) -> dict[str, Any]:
    host = application.computer_host_status
    if host is None:
        return {
            "enabled": False,
            "available": False,
            "platform": "unknown",
            "runtime": None,
            "reason": "not_configured",
            "helper_path": None,
            "permissions": {
                "accessibility": accessibility,
                "screen_recording": screen,
            },
            "lease": lease,
        }
    return {
        "enabled": host.enabled,
        "available": host.available,
        "platform": host.platform,
        "runtime": host.runtime,
        "reason": host.reason,
        "helper_path": host.helper_path,
        "permissions": {
            "accessibility": accessibility,
            "screen_recording": screen,
        },
        "lease": lease,
    }


async def computer_status(
    params: dict[str, Any], ctx: RpcContext
) -> dict[str, Any]:
    application = ctx.application
    accessibility, screen = await _live_permissions(application)
    return _status_dict(
        application,
        accessibility=accessibility,
        screen=screen,
        lease=_lease_dict(application),
    )


async def computer_request_permission(
    params: dict[str, Any], ctx: RpcContext
) -> dict[str, Any]:
    permission = params.get("permission")
    if not isinstance(permission, str) or permission not in _PERMISSIONS:
        raise JsonRpcError(
            RpcErrorCode.INVALID_PARAMS,
            "permission must be 'accessibility' or 'screen_recording'",
        )

    application = ctx.application
    runtime = application.computer_runtime
    if runtime is None:
        raise JsonRpcError(INVALID_STATE, "computer runtime unavailable")
    client = getattr(runtime, "helper_client", None)
    if client is None:
        raise JsonRpcError(INVALID_STATE, "computer runtime unavailable")

    # 这是只读权限请求（不弹自动授权），仅此处允许 prompt=true。
    try:
        await client.ensure_started()
        if permission == "accessibility":
            await client.call("accessibility_status", {"prompt": True})
        else:
            await client.call("screen_capture_status", {"prompt": True})
    except Exception as exc:
        logger.warning("computer permission request failed: %s", exc)
        raise JsonRpcError(
            RpcErrorCode.INTERNAL_ERROR, "permission request failed"
        ) from exc

    # 返回最新状态（请求后继续轮询，不假装瞬间 granted）。
    return await computer_status({}, ctx)


def _extract_observation(output: str) -> dict[str, Any] | None:
    """解析 computer_observe 的 ToolResult.output JSON；非法返回 None。"""

    try:
        payload = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    if not isinstance(payload.get("id"), str):
        return None
    return payload


async def computer_latest_observation(
    params: dict[str, Any], ctx: RpcContext
) -> dict[str, Any]:
    application = ctx.application
    run_id = params.get("run_id")
    if run_id is not None and (not isinstance(run_id, str) or not run_id):
        raise JsonRpcError(RpcErrorCode.INVALID_PARAMS, "run_id must be a string")

    if not run_id:
        lease = application.computer_lease
        if lease is not None:
            run_id = lease.snapshot.owner_run_id
        if not run_id:
            return {"run_id": None, "event_time": None, "observation": None}

    trace_store = application.trace_store
    if trace_store is None:
        return {"run_id": run_id, "event_time": None, "observation": None}

    try:
        events = await trace_store.load_events(run_id)
    except Exception as exc:
        logger.warning("computer latest observation load failed: %s", exc)
        return {"run_id": run_id, "event_time": None, "observation": None}

    # 取该 Run 最新一次成功的 computer_observe（从后往前找）。
    for event in reversed(events):
        if event.type is not AgentEventType.TOOL_COMPLETED:
            continue
        if event.tool_call is None or event.tool_result is None:
            continue
        if event.tool_call.name != "computer_observe":
            continue
        if not event.tool_result.success or not event.tool_result.output:
            continue
        observation = _extract_observation(event.tool_result.output)
        if observation is None:
            continue
        return {
            "run_id": run_id,
            "event_time": (
                event.event_time.isoformat()
                if event.event_time is not None
                else None
            ),
            "observation": observation,
        }

    return {"run_id": run_id, "event_time": None, "observation": None}


def register(dispatcher: RpcDispatcher) -> None:
    dispatcher.register("computer.status", computer_status)
    dispatcher.register(
        "computer.request_permission", computer_request_permission
    )
    dispatcher.register(
        "computer.latest_observation", computer_latest_observation
    )
