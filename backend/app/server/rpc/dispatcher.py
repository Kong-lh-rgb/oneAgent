"""RPC method 注册表与派发。

所有 RPC method 必须显式 ``register``，禁止 ``getattr(application, method)`` /
``eval`` / 任意字符串调用 Python 对象。handler 接收 ``(params, ctx)``：
- ``params``：请求参数 dict；
- ``ctx``：``RpcContext``，至少持有 Application 与当前 RpcConnection。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from .protocol import JsonRpcError, RpcErrorCode

if TYPE_CHECKING:
    from app.application import Application

    from .connection import RpcConnection

logger = logging.getLogger("oneagent.server.rpc.dispatcher")

Handler = Callable[[dict[str, Any], "RpcContext"], Awaitable[Any]]


class RpcContext:
    """一次 RPC 调用的上下文。"""

    def __init__(
        self,
        application: Application,
        connection: RpcConnection,
    ) -> None:
        self.application = application
        self.connection = connection


class RpcDispatcher:
    """显式 method 注册表。"""

    def __init__(self) -> None:
        self._methods: dict[str, Handler] = {}

    def register(self, method: str, handler: Handler) -> None:
        if not method or not method.strip():
            raise ValueError("rpc method name cannot be empty")
        if method in self._methods:
            raise ValueError(f"duplicate rpc method: {method}")
        self._methods[method] = handler

    def register_many(self, handlers: dict[str, Handler]) -> None:
        for name, handler in handlers.items():
            self.register(name, handler)

    def has_method(self, method: str) -> bool:
        return method in self._methods

    async def dispatch(
        self,
        method: str,
        params: dict[str, Any],
        ctx: RpcContext,
    ) -> Any:
        """派发一个 method；未注册 → Method not found；异常 → Internal error。"""

        handler = self._methods.get(method)
        if handler is None:
            raise JsonRpcError(
                RpcErrorCode.METHOD_NOT_FOUND,
                f"Method not found: {method}",
            )
        try:
            return await handler(params, ctx)
        except JsonRpcError:
            raise
        except Exception as exc:  # noqa: BLE001 - 不向客户端泄漏 traceback
            logger.exception("rpc method %r failed", method)
            raise JsonRpcError(
                RpcErrorCode.INTERNAL_ERROR,
                "Internal error",
            ) from exc


def rpc_method(name: str) -> Callable[[Handler], Handler]:
    """装饰器式注册标记：配合 ``register_all_decorated`` 使用。"""

    def decorate(fn: Handler) -> Handler:
        fn._rpc_name = name  # type: ignore[attr-defined]
        return fn

    return decorate


__all__ = ["Handler", "RpcContext", "RpcDispatcher", "rpc_method"]
