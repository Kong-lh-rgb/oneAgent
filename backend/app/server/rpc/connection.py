"""RpcConnection：单条 WebSocket 的双向 JSON-RPC 通道。

关键点：
- receive loop 对每条消息创建独立 asyncio task 处理，长请求
  （如 conversation.send 执行 30 秒）不阻塞后续消息接收 —— 同一个 Desktop
  在 Agent 执行过程中仍能发 run.cancel；
- send 通过 asyncio.Lock 串行，保证 response / notification 并发写同一 socket
  不乱序；
- Desktop 断开 ≠ Run 取消：连接断开只停止发送，不会取消已启动的 Agent Run，
  Run 只能通过 RunManager.cancel() 显式取消。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from .dispatcher import RpcContext, RpcDispatcher
from .protocol import JSONRPC_VERSION, JsonRpcError, parse_message

if TYPE_CHECKING:
    from app.application import Application

    from .hub import RpcHub

logger = logging.getLogger("oneagent.server.rpc.connection")


def _to_jsonable(obj: Any) -> Any:
    """把 pydantic 模型（含嵌套）递归转成纯 JSON 结构。"""

    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, tuple):
        return [_to_jsonable(item) for item in obj]
    if isinstance(obj, list):
        return [_to_jsonable(item) for item in obj]
    if isinstance(obj, dict):
        return {key: _to_jsonable(value) for key, value in obj.items()}
    return obj


class RpcConnection:
    """一条 WS /rpc 连接：接收请求/通知，发送响应/通知。"""

    def __init__(
        self,
        websocket: WebSocket,
        dispatcher: RpcDispatcher,
        application: Application,
        hub: RpcHub,
    ) -> None:
        self._websocket = websocket
        self._dispatcher = dispatcher
        self._application = application
        self._hub = hub
        self._send_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()
        self._closed = False

    @property
    def application(self) -> Application:
        return self._application

    @property
    def hub(self) -> RpcHub:
        return self._hub

    @property
    def is_closed(self) -> bool:
        return self._closed

    # ------------------------------------------------------------------
    # 接收循环
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """接收循环：每条消息独立 task，立即回到 receive。"""

        try:
            while not self._closed:
                text = await self._websocket.receive_text()
                task = asyncio.create_task(self._handle_message(text))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
        except WebSocketDisconnect:
            pass
        finally:
            self._closed = True
            # 不取消已启动的 task（长 Agent Run 会继续执行，仅停止发送）。

    async def _handle_message(self, text: str) -> None:
        parsed = parse_message(text)
        if parsed.error is not None:
            await self.send_error(parsed.id, parsed.error)
            return
        ctx = RpcContext(self._application, self)
        if parsed.notification is not None:
            await self._dispatch_notification(
                parsed.notification.method,
                parsed.notification.params,
                ctx,
            )
            return
        assert parsed.request is not None
        request = parsed.request
        try:
            result = await self._dispatcher.dispatch(
                request.method,
                request.params,
                ctx,
            )
        except JsonRpcError as exc:
            await self.send_error(request.id, exc)
            return
        await self.send_response(request.id, result)

    async def _dispatch_notification(
        self,
        method: str,
        params: dict[str, Any],
        ctx: RpcContext,
    ) -> None:
        try:
            await self._dispatcher.dispatch(method, params, ctx)
        except JsonRpcError:
            pass  # 通知无需响应
        except Exception:  # noqa: BLE001
            logger.exception("rpc notification %r failed", method)

    # ------------------------------------------------------------------
    # 发送（串行）
    # ------------------------------------------------------------------

    async def _send(self, payload: dict[str, Any]) -> None:
        if self._closed:
            return
        text = json.dumps(_to_jsonable(payload), ensure_ascii=False)
        async with self._send_lock:
            if self._closed:
                return
            try:
                await self._websocket.send_text(text)
            except Exception:  # noqa: BLE001 - 断线后停止发送
                self._closed = True

    async def send_response(self, request_id: str | int, result: Any) -> None:
        await self._send(
            {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}
        )

    async def send_error(
        self,
        request_id: str | int | None,
        error: JsonRpcError,
    ) -> None:
        await self._send(
            {
                "jsonrpc": JSONRPC_VERSION,
                "id": request_id,
                "error": error.to_body(),
            }
        )

    async def send_notification(self, method: str, params: Any) -> None:
        await self._send(
            {
                "jsonrpc": JSONRPC_VERSION,
                "method": method,
                "params": _to_jsonable(params),
            }
        )


__all__ = ["RpcConnection"]
