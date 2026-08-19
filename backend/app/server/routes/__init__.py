"""Agent Server 路由组装。

所有路由都从 ``request.app.state.application`` 取 Application，只调用现有
领域服务（ConversationService / RunManager / AutomationScheduler / TraceStore），
不在 Server 层复制 load → start → save 逻辑。
"""

from __future__ import annotations

from fastapi import APIRouter

from . import automations, conversations, events, runs

api_router = APIRouter(prefix="/api")
api_router.include_router(conversations.router)
api_router.include_router(runs.router)
api_router.include_router(automations.router)
api_router.include_router(events.router)

__all__ = ["api_router"]
