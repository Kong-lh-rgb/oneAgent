"""基于官方 SDK 的 stdio MCP 客户端。"""

from __future__ import annotations

import asyncio
import json
import os
import re
from contextlib import AsyncExitStack
from typing import Any, Protocol

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult

from .errors import MCPConnectionError, MCPToolCallError, MCPToolDiscoveryError
from .models import MCPRemoteTool, MCPServerConfig

_ENV_REFERENCE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


class MCPClientProtocol(Protocol):
    """管理器依赖的最小客户端接口，便于离线替换。"""

    async def start(self) -> None: ...

    async def list_tools(self) -> tuple[MCPRemoteTool, ...]: ...

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str: ...

    async def close(self) -> None: ...


class StdioMCPClient:
    """维护一个 MCP stdio 子进程及其 ClientSession。"""

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    async def start(self) -> None:
        """启动子进程并完成 MCP initialize 握手。"""

        if self._session is not None:
            return
        stack = AsyncExitStack()
        try:
            async with asyncio.timeout(self.config.startup_timeout_seconds):
                streams = await stack.enter_async_context(
                    stdio_client(
                        StdioServerParameters(
                            command=self.config.command,
                            args=list(self.config.args),
                            env=_resolve_environment(self.config),
                            cwd=self.config.cwd,
                        )
                    )
                )
                session = await stack.enter_async_context(ClientSession(*streams))
                await session.initialize()
        except Exception as exc:
            await _close_quietly(stack)
            raise MCPConnectionError(
                f"MCP Server '{self.config.name}' 启动失败: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        self._stack = stack
        self._session = session

    async def list_tools(self) -> tuple[MCPRemoteTool, ...]:
        """发现服务器暴露的全部工具。"""

        session = self._require_session()
        try:
            async with asyncio.timeout(self.config.call_timeout_seconds):
                result = await session.list_tools()
        except Exception as exc:
            raise MCPToolDiscoveryError(
                f"MCP Server '{self.config.name}' 工具发现失败: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        return tuple(
            MCPRemoteTool(
                name=tool.name,
                description=tool.description or "",
                input_schema=tool.inputSchema,
            )
            for tool in result.tools
        )

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """调用远端工具并把 MCP 内容转换为文本。"""

        session = self._require_session()
        try:
            async with asyncio.timeout(self.config.call_timeout_seconds):
                result = await session.call_tool(name, arguments)
        except Exception as exc:
            raise MCPToolCallError(
                f"MCP 工具 '{self.config.name}/{name}' 调用失败: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        output = serialize_mcp_result(result)
        if result.isError:
            raise MCPToolCallError(
                f"MCP 工具 '{self.config.name}/{name}' 返回错误: {output}"
            )
        return output

    async def close(self) -> None:
        """关闭会话与 stdio 子进程；可重复调用。"""

        stack = self._stack
        self._stack = None
        self._session = None
        if stack is not None:
            await stack.aclose()

    def _require_session(self) -> ClientSession:
        if self._session is None:
            raise MCPConnectionError(
                f"MCP Server '{self.config.name}' 尚未连接"
            )
        return self._session


def serialize_mcp_result(result: CallToolResult) -> str:
    """保留 MCP 多段内容与 structuredContent，不丢失非文本结果。"""

    if len(result.content) == 1 and result.content[0].type == "text":
        text = result.content[0].text
        if result.structuredContent is None:
            return text
    payload: dict[str, Any] = {
        "content": [
            item.model_dump(mode="json", by_alias=True, exclude_none=True)
            for item in result.content
        ]
    }
    if result.structuredContent is not None:
        payload["structured_content"] = result.structuredContent
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


async def _close_quietly(stack: AsyncExitStack) -> None:
    try:
        await stack.aclose()
    except BaseException:
        pass


def _resolve_environment(config: MCPServerConfig) -> dict[str, str] | None:
    """解析配置中的环境变量引用，避免把密钥直接写入 JSON。"""

    resolved: dict[str, str] = {}
    for key, value in config.env.items():
        match = _ENV_REFERENCE.fullmatch(value)
        if match is None:
            resolved[key] = value
            continue
        variable = match.group(1)
        if variable not in os.environ:
            raise MCPConnectionError(
                f"MCP Server '{config.name}' 缺少环境变量 {variable}"
            )
        resolved[key] = os.environ[variable]
    return resolved or None


__all__ = ["MCPClientProtocol", "StdioMCPClient", "serialize_mcp_result"]
