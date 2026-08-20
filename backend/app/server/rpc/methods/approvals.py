"""approval RPC methods：approval.list / get / approve / deny。

approve / deny 由 DesktopApprovalGate 处理：持久化 resolve（只能一次）+
唤醒正在等待的 Run task + 广播 approval.resolved。通知 approval.required /
approval.resolved 由 Gate 的 broadcaster（WebSocket hub）发出，这里不重复。
"""

from __future__ import annotations

from typing import Any

from app.approval import ApprovalRequestStatus

from ..dispatcher import RpcContext, RpcDispatcher
from ..protocol import (
    INVALID_STATE,
    RESOURCE_NOT_FOUND,
    JsonRpcError,
    RpcErrorCode,
)


async def approval_list(params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
    status = params.get("status")
    if status is not None:
        try:
            status = ApprovalRequestStatus(status)
        except ValueError as exc:
            raise JsonRpcError(
                RpcErrorCode.INVALID_PARAMS,
                f"invalid status: {status}",
            ) from exc
    limit = _positive_int(params, "limit", default=50)
    approvals = await ctx.application.approval_store.list(
        status=status,
        limit=limit,
    )
    return {"approvals": approvals, "count": len(approvals)}


async def approval_get(params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
    approval_id = _require_str(params, "approval_id")
    approval = await ctx.application.approval_store.get(approval_id)
    if approval is None:
        raise JsonRpcError(RESOURCE_NOT_FOUND, "approval not found")
    return {"approval": approval}


async def approval_approve(params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
    gate = _require_gate(ctx)
    approval_id = _require_str(params, "approval_id")
    try:
        approval = await gate.approve(approval_id)
    except (KeyError, ValueError) as exc:
        raise JsonRpcError(INVALID_STATE, str(exc)) from exc
    return {"approval": approval}


async def approval_deny(params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
    gate = _require_gate(ctx)
    approval_id = _require_str(params, "approval_id")
    try:
        approval = await gate.deny(approval_id)
    except (KeyError, ValueError) as exc:
        raise JsonRpcError(INVALID_STATE, str(exc)) from exc
    return {"approval": approval}


def _require_gate(ctx: RpcContext) -> Any:
    gate = ctx.application.desktop_approval_gate
    if gate is None:
        raise JsonRpcError(INVALID_STATE, "desktop approval gate not available")
    return gate


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
    dispatcher.register("approval.list", approval_list)
    dispatcher.register("approval.get", approval_get)
    dispatcher.register("approval.approve", approval_approve)
    dispatcher.register("approval.deny", approval_deny)
