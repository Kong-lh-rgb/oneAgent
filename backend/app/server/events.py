"""进程内 WebSocket 事件广播：把现有 AgentEvent 实时推送给 Desktop 客户端。

事件流（不新增第二套 AgentEvent）：

    AgentRuntime → AgentEvent
        ↓ CompositeEventHandler
    SQLiteTraceEventHandler          （持久化）
    + DesktopBroadcastEventHandler → EventBroker → WebSocket clients

``EventBroker`` 只负责维护连接与广播；``DesktopBroadcastEventHandler`` 是
``AgentEventHandler``，由 ConversationService 作为 shared_event_handler 注入，
因此 Desktop 手动触发的 Run 与 Automation 触发的 Run 最终都进入同一条
broadcast path。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket

from app.agent.events import AgentEvent, AgentEventHandler, AgentEventType

logger = logging.getLogger("oneagent.server.events")


class EventBroker:
    """维护已连接的 WebSocket 客户端并把消息 JSON 广播给所有客户端。"""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._clients.add(websocket)
        logger.debug("event client connected (%d connected)", len(self._clients))

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(websocket)
        logger.debug("event client disconnected (%d connected)", len(self._clients))

    async def publish(self, message: dict[str, Any]) -> None:
        """把一条 JSON 消息广播给所有客户端（单个客户端失败不影响其余）。"""

        async with self._lock:
            clients = tuple(self._clients)
        if not clients:
            return
        text = json.dumps(message, ensure_ascii=False, default=str)
        for websocket in clients:
            try:
                await websocket.send_text(text)
            except Exception:  # noqa: BLE001 - 断线连接由 disconnect 清理
                continue

    async def publish_event(self, event: AgentEvent) -> None:
        """广播一条 AgentEvent（复用现有事件模型，不另造第二套）。"""

        await self.publish(
            {"type": "agent_event", "data": event.model_dump(mode="json")}
        )

    async def publish_run_status(self, run_id: str, status: str) -> None:
        """广播 Run 生命周期状态变化（轻量 envelope，非 AgentEvent）。"""

        await self.publish(
            {
                "type": "run_status",
                "data": {"run_id": run_id, "status": status},
            }
        )

    @property
    def client_count(self) -> int:
        return len(self._clients)


class DesktopBroadcastEventHandler(AgentEventHandler):
    """把 AgentEvent 转发给 EventBroker；终态事件额外广播 run_status。"""

    def __init__(self, broker: EventBroker) -> None:
        self._broker = broker

    async def emit(self, event: AgentEvent) -> None:
        await self._broker.publish_event(event)
        status = _derived_run_status(event)
        if status is not None:
            await self._broker.publish_run_status(event.run_id, status)


def _derived_run_status(event: AgentEvent) -> str | None:
    """从 AgentEvent 推导 Run 生命周期状态（覆盖 realtime 所需的主要变化）。"""

    if event.type is AgentEventType.AGENT_STARTED:
        return "running"
    if event.type is AgentEventType.AGENT_COMPLETED:
        return "completed"
    if event.type is AgentEventType.AGENT_FAILED:
        return "failed"
    return None


__all__ = ["DesktopBroadcastEventHandler", "EventBroker"]
