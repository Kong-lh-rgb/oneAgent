"""JSON-RPC 2.0 协议层（自己实现，不引入成熟 framework）。

支持 request / response / error / notification；暂不支持 batch。
- params V0 只支持 JSON object（不接受 positional array）；
- id 支持 string / integer；
- 标准错误码 + 自定义业务错误（不暴露 Python traceback）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

JSONRPC_VERSION = "2.0"


class RpcErrorCode(IntEnum):
    """JSON-RPC 2.0 标准错误码。"""

    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603


# 自定义业务错误（位于标准 code 之外的 -32000 段，不泄漏 traceback）。
RESOURCE_NOT_FOUND = -32000
INVALID_STATE = -32001


class JsonRpcError(Exception):
    """带 code / message / data 的协议错误。"""

    def __init__(
        self,
        code: int,
        message: str,
        data: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = int(code)
        self.message = message
        self.data = data

    def to_body(self) -> dict[str, Any]:
        body: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            body["data"] = self.data
        return body


@dataclass
class RpcRequest:
    """带 id 的请求（需要响应）。"""

    id: str | int
    method: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class RpcNotification:
    """不带 id 的通知（无需响应）。"""

    method: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedMessage:
    """parse_message 的解析结果（请求 / 通知 / 错误三选一）。"""

    request: RpcRequest | None = None
    notification: RpcNotification | None = None
    error: JsonRpcError | None = None
    id: str | int | None = None


def parse_message(text: str) -> ParsedMessage:
    """解析一条 WS 文本为 JSON-RPC 消息；协议错误不抛异常而是放进结果。"""

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return ParsedMessage(
            error=JsonRpcError(RpcErrorCode.PARSE_ERROR, "Parse error")
        )

    if not isinstance(payload, dict):
        return ParsedMessage(
            error=JsonRpcError(
                RpcErrorCode.INVALID_REQUEST,
                "Invalid Request: message must be an object",
            )
        )

    raw_id = payload.get("id")
    if isinstance(raw_id, bool) or (
        raw_id is not None and not isinstance(raw_id, (str, int))
    ):
        return ParsedMessage(
            error=JsonRpcError(
                RpcErrorCode.INVALID_REQUEST,
                "Invalid Request: id must be a string or integer",
            )
        )
    request_id = _id_or_none(raw_id)

    if payload.get("jsonrpc") != JSONRPC_VERSION:
        return ParsedMessage(
            error=JsonRpcError(
                RpcErrorCode.INVALID_REQUEST,
                "Invalid Request: jsonrpc must be '2.0'",
            ),
            id=request_id,
        )

    method = payload.get("method")
    if not isinstance(method, str) or not method:
        return ParsedMessage(
            error=JsonRpcError(
                RpcErrorCode.INVALID_REQUEST,
                "Invalid Request: method is required",
            ),
            id=request_id,
        )

    params = payload.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return ParsedMessage(
            error=JsonRpcError(
                RpcErrorCode.INVALID_REQUEST,
                "Invalid Request: params must be an object",
            ),
            id=request_id,
        )

    if raw_id is None:
        return ParsedMessage(
            notification=RpcNotification(method=method, params=params)
        )
    return ParsedMessage(
        request=RpcRequest(id=raw_id, method=method, params=params),
        id=raw_id,
    )


def _id_or_none(value: object) -> str | int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (str, int)):
        return value
    return None


__all__ = [
    "INVALID_STATE",
    "JSONRPC_VERSION",
    "RESOURCE_NOT_FOUND",
    "JsonRpcError",
    "ParsedMessage",
    "RpcErrorCode",
    "RpcNotification",
    "RpcRequest",
    "parse_message",
]
