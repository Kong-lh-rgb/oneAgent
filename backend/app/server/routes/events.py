"""WebSocket 实时事件：Desktop 通过 ``WS /api/events`` 订阅 AgentEvent。"""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter(tags=["events"])


@router.websocket("/events")
async def events_endpoint(websocket: WebSocket) -> None:
    broker = websocket.app.state.broker
    await broker.connect(websocket)
    try:
        # 保持连接；忽略客户端消息（V0 无下行控制协议）。
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await broker.disconnect(websocket)


__all__ = ["router"]
