"""长期记忆只读 RPC。

Desktop 只能观察 Core、active 与 archived 记忆；记忆写入仍由
Agent 提出、Harness 验证并执行，不在此暴露可变更接口。
"""

from __future__ import annotations

from typing import Any

from ..dispatcher import RpcContext, RpcDispatcher


async def memory_list(
    params: dict[str, Any], ctx: RpcContext
) -> dict[str, Any]:
    del params
    manager = ctx.application.memory_manager
    if manager is None:
        return {
            "core": "",
            "active": [],
            "archived": [],
            "active_count": 0,
            "max_active": 0,
        }

    core = await manager.core.load()
    active = await manager.list()
    archived = await manager.list_archived()
    return {
        "core": core,
        "active": [record.model_dump(mode="json") for record in active],
        "archived": [record.model_dump(mode="json") for record in archived],
        "active_count": len(active),
        "max_active": manager.max_active,
    }


def register(dispatcher: RpcDispatcher) -> None:
    dispatcher.register("memory.list", memory_list)


__all__ = ["memory_list", "register"]
