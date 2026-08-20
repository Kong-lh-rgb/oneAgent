"""把远端 MCP 工具适配成 Vesta BaseTool。"""

from __future__ import annotations

from typing import Any

from app.models.types import ToolDefinition, ToolPermission
from app.tools.base import BaseTool

from .client import MCPClientProtocol
from .models import MCPRemoteTool


class MCPToolAdapter(BaseTool):
    """让 MCP 工具复用现有权限、Hook、超时和日志执行链。"""

    def __init__(
        self,
        *,
        server_name: str,
        registered_name: str,
        remote_tool: MCPRemoteTool,
        client: MCPClientProtocol,
        permission: ToolPermission,
    ) -> None:
        self.server_name = server_name
        self.remote_name = remote_tool.name
        self._client = client
        self._definition = ToolDefinition(
            name=registered_name,
            description=(
                f"[MCP: {server_name}] {remote_tool.description}"
                if remote_tool.description
                else f"由 MCP Server '{server_name}' 提供的工具"
            ),
            parameters=remote_tool.input_schema,
            permission=permission,
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, arguments: dict[str, Any]) -> str:
        return await self._client.call_tool(self.remote_name, arguments)


__all__ = ["MCPToolAdapter"]
