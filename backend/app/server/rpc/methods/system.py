"""system RPC methods。"""

from __future__ import annotations

from typing import Any

from app.server.version import __version__

from ..dispatcher import RpcContext, RpcDispatcher


async def system_info(params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
    """Host 基础信息（Settings / 开发环境判断用）。"""

    application = ctx.application
    return {
        "status": "ok",
        "provider": application.provider,
        "model": application.model,
        "version": __version__,
        "database": str(application.database),
    }


def register(dispatcher: RpcDispatcher) -> None:
    dispatcher.register("system.info", system_info)
