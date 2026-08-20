"""Agent Server：本地 Host transport。

FastAPI 不是业务架构，只负责：

- process lifespan（``application.start()`` / ``application.close()``）
- WebSocket upgrade（``WS /rpc``）
- ``GET /health``（供 Electron / 开发环境判断 Host 是否启动）

Renderer 的正常业务全部走 ``WS /rpc``（JSON-RPC 2.0，一条连接双向通信），
不再使用 REST CRUD。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket

from app.application import Application

from .rpc import (
    RpcBroadcastEventHandler,
    RpcConnection,
    RpcHub,
    build_dispatcher,
)
from .version import __version__

logger = logging.getLogger("oneagent.server")


def create_app(application: Application | None = None) -> FastAPI:
    """构造 Agent Server 应用。

    ``application`` 为 None 时自动用默认配置创建（provider 从 .env 选择）。
    调用方也可传入已配置的 Application（例如测试注入离线 fake registry）。
    """

    if application is None:
        application = Application()

    # 全局共享事件观察者：在 application.start() 之前注入，
    # ConversationService 构造时会把 RPC 广播与 Trace 一起组合。
    hub = RpcHub()
    broadcast = RpcBroadcastEventHandler(hub)
    application.shared_event_handler = broadcast

    dispatcher = build_dispatcher()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await application.start()
        logger.info(
            "oneagent server started · provider=%s · model=%s",
            application.provider,
            application.model,
        )
        try:
            yield
        finally:
            await application.close()
            logger.info("oneagent server stopped")

    app = FastAPI(
        title="OneAgent Agent Server",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.application = application
    app.state.hub = hub
    app.state.dispatcher = dispatcher

    @app.get("/health")
    async def health(request: Request) -> dict[str, object]:
        current: Application = request.app.state.application
        return {
            "status": "ok",
            "provider": current.provider,
            "model": current.model,
            "version": __version__,
        }

    @app.websocket("/rpc")
    async def rpc_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        connection = RpcConnection(websocket, dispatcher, application, hub)
        await hub.register(connection)
        try:
            await connection.run()
        finally:
            await hub.unregister(connection)

    return app


__all__ = ["__version__", "create_app"]

