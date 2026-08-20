"""task RPC methods（Plan Mode V1 最小接口）。

- ``task.get``：按 ID / 前缀获取任务详情；
- ``task.plan_accept``：PENDING → ACTIVE（用户接受计划）；
- ``task.plan_reject``：PENDING → CANCELLED（用户拒绝计划）。

只有 PENDING 任务可以被 accept / reject；已 ACTIVE / COMPLETED / CANCELLED
的任务不允许再次操作（由 FileTaskStore 在锁内校验）。
"""

from __future__ import annotations

from typing import Any

from ..dispatcher import RpcContext, RpcDispatcher
from ..protocol import (
    INVALID_STATE,
    RESOURCE_NOT_FOUND,
    JsonRpcError,
    RpcErrorCode,
)


async def task_get(params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
    task_id = _require_str(params, "task_id")
    task = await ctx.application.task_store.resolve(task_id)
    if task is None:
        raise JsonRpcError(RESOURCE_NOT_FOUND, "task not found")
    return {"task": task}


async def task_plan_accept(params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
    task_id = _require_str(params, "task_id")
    try:
        task = await ctx.application.task_store.plan_accept(task_id)
    except KeyError as exc:
        raise JsonRpcError(RESOURCE_NOT_FOUND, "task not found") from exc
    except ValueError as exc:
        raise JsonRpcError(INVALID_STATE, str(exc)) from exc
    return {"task": task}


async def task_plan_reject(params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
    task_id = _require_str(params, "task_id")
    try:
        task = await ctx.application.task_store.plan_reject(task_id)
    except KeyError as exc:
        raise JsonRpcError(RESOURCE_NOT_FOUND, "task not found") from exc
    except ValueError as exc:
        raise JsonRpcError(INVALID_STATE, str(exc)) from exc
    return {"task": task}


def _require_str(params: dict[str, Any], key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value:
        raise JsonRpcError(RpcErrorCode.INVALID_PARAMS, f"{key} is required")
    return value


def register(dispatcher: RpcDispatcher) -> None:
    dispatcher.register("task.get", task_get)
    dispatcher.register("task.plan_accept", task_plan_accept)
    dispatcher.register("task.plan_reject", task_plan_reject)
