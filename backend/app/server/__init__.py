"""Vesta Host：本地 FastAPI + JSON-RPC WebSocket transport。"""

from .app import create_app
from .rpc import (
    JsonRpcError,
    RpcBroadcastEventHandler,
    RpcConnection,
    RpcContext,
    RpcDispatcher,
    RpcErrorCode,
    RpcHub,
    build_dispatcher,
    parse_message,
)
from .version import __version__

__all__ = [
    "JsonRpcError",
    "RpcBroadcastEventHandler",
    "RpcConnection",
    "RpcContext",
    "RpcDispatcher",
    "RpcErrorCode",
    "RpcHub",
    "build_dispatcher",
    "create_app",
    "parse_message",
    "__version__",
]
