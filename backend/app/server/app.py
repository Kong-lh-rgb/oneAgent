"""Agent Server：把现有 Agent 能力通过 HTTP / WebSocket 暴露给 Desktop。

FastAPI lifespan 内：``await application.start()``；shutdown：
``await application.close()``（正确关闭 Scheduler / MCP / 模型适配器）。

核心链路保持不变：React Renderer → Python Agent Server → ConversationService
→ RunManager → AgentRuntime；WebSocket 广播复用现有 AgentEvent。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.application import Application

from .events import DesktopBroadcastEventHandler, EventBroker
from .routes import api_router

logger = logging.getLogger("oneagent.server")

__version__ = "0.1.0"


def create_app(application: Application | None = None) -> FastAPI:
    """构造 Agent Server 应用。

    ``application`` 为 None 时自动用默认配置创建（provider 从 .env 选择）。
    调用方也可传入已配置的 Application（例如测试注入离线 fake registry）。
    """

    if application is None:
        application = Application()

    # 全局共享事件观察者：在 application.start() 之前注入，
    # ConversationService 构造时会把 Desktop 广播与 Trace 一起组合。
    broker = EventBroker()
    broadcast = DesktopBroadcastEventHandler(broker)
    application.shared_event_handler = broadcast

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
    app.state.broker = broker

    # V0 本地开发：允许 Vite dev origin 与 Electron file://（无鉴权）。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health(request: Request) -> dict[str, object]:
        current: Application = request.app.state.application
        return {
            "status": "ok",
            "provider": current.provider,
            "model": current.model,
            "version": __version__,
        }

    app.include_router(api_router)
    return app


__all__ = ["__version__", "create_app"]
