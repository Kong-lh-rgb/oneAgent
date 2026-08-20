"""RpcHub：维护所有已连接的 RpcConnection，并向它们广播 JSON-RPC notification。

链路保持：

    AgentRuntime → AgentEvent
        ↓ CompositeEventHandler
    SQLiteTraceEventHandler（持久化）
    + RpcBroadcastEventHandler → RpcHub → 各 WebSocket 客户端

Automation 与 manual dispatch 都经过 ConversationService 的 shared_event_handler，
因此走同一条广播链。
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from app.agent.events import AgentEvent, AgentEventHandler, AgentEventType

if TYPE_CHECKING:
    from .connection import RpcConnection

logger = logging.getLogger("oneagent.server.rpc.hub")


class RpcHub:
    """进程内维护已连接的 RPC 连接并广播 notification。"""

    def __init__(self) -> None:
        self._connections: set[RpcConnection] = set()
        self._lock = asyncio.Lock()

    async def register(self, connection: RpcConnection) -> None:
        async with self._lock:
            self._connections.add(connection)
        logger.debug("rpc connection registered (%d)", len(self._connections))

    async def unregister(self, connection: RpcConnection) -> None:
        async with self._lock:
            self._connections.discard(connection)
        logger.debug("rpc connection unregistered (%d)", len(self._connections))

    async def broadcast(self, method: str, params: Any) -> None:
        """向所有已连接客户端广播一条 notification（单个失败不影响其余）。"""

        async with self._lock:
            connections = tuple(self._connections)
        for connection in connections:
            await connection.send_notification(method, params)

    @property
    def connection_count(self) -> int:
        return len(self._connections)


class RpcBroadcastEventHandler(AgentEventHandler):
    """把 AgentEvent 转成 JSON-RPC notification 广播（agent.event / run.status）。

    复用现有 AgentEvent，不新增第二套事件模型。终态 AgentEvent 额外推导
    run.status（realtime UI 需要）；cancel 等无 AgentEvent 的状态变化由
    RPC method 自行广播 run.status。
    """

    def __init__(self, hub: RpcHub) -> None:
        self._hub = hub

    async def emit(self, event: AgentEvent) -> None:
        await self._hub.broadcast("agent.event", event.model_dump(mode="json"))
        status = _derived_run_status(event)
        if status is not None:
            await self._hub.broadcast(
                "run.status",
                {"run_id": event.run_id, "status": status},
            )


def _derived_run_status(event: AgentEvent) -> str | None:
    """从 AgentEvent 推导 Run 生命周期状态（覆盖 realtime 所需的主要变化）。"""

    if event.type is AgentEventType.AGENT_STARTED:
        return "running"
    if event.type is AgentEventType.AGENT_COMPLETED:
        return "completed"
    if event.type is AgentEventType.AGENT_FAILED:
        return "failed"
    return None


__all__ = ["RpcBroadcastEventHandler", "RpcHub"]
