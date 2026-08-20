"""RPC methods 注册。"""

from __future__ import annotations

from ..dispatcher import RpcDispatcher
from . import (
    approvals,
    automations,
    computer,
    conversations,
    runs,
    system,
    tasks,
    trace,
)


def build_dispatcher() -> RpcDispatcher:
    """构建并注册全部 RPC method（显式注册，不动态调用）。"""

    dispatcher = RpcDispatcher()
    system.register(dispatcher)
    conversations.register(dispatcher)
    runs.register(dispatcher)
    trace.register(dispatcher)
    automations.register(dispatcher)
    approvals.register(dispatcher)
    tasks.register(dispatcher)
    computer.register(dispatcher)
    return dispatcher


__all__ = ["build_dispatcher"]
