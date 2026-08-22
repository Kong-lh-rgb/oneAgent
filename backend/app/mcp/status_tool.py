"""供 Agent 读取当前 MCP 运行状态的轻量只读工具。"""

from __future__ import annotations

from typing import Any

from app.models.types import ToolDefinition, ToolPermission
from app.tools.base import BaseTool

from .manager import MCPClientManager

MCP_STATUS_TOOL_NAME = "mcp_status"


class MCPStatusTool(BaseTool):
    """直接读取现有 Manager 快照，不启动或调用任何 MCP Server。"""

    definition = ToolDefinition(
        name=MCP_STATUS_TOOL_NAME,
        description=(
            "List configured MCP servers, their live connection status, errors, "
            "and registered MCP tool names. Use this for questions about which "
            "MCP servers or MCP tools are currently available. This is read-only, "
            "does not start servers, and does not require approval."
        ),
        parameters={
            "type": "object",
            "properties": {
                "server": {
                    "type": "string",
                    "description": "Optional exact MCP server name to inspect.",
                },
                "include_tools": {
                    "type": "boolean",
                    "description": (
                        "Whether to include registered tool names; defaults to true."
                    ),
                    "default": True,
                },
            },
            "additionalProperties": False,
        },
        permission=ToolPermission.ALLOWED,
    )

    def __init__(
        self,
        manager: MCPClientManager | None,
        *,
        configuration_error: str | None = None,
    ) -> None:
        self._manager = manager
        self._configuration_error = configuration_error

    async def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        server = arguments.get("server")
        if server is not None and (
            not isinstance(server, str) or not server.strip()
        ):
            raise TypeError("server 必须是非空字符串")
        include_tools = arguments.get("include_tools", True)
        if not isinstance(include_tools, bool):
            raise TypeError("include_tools 必须是布尔值")

        statuses = self._manager.statuses() if self._manager is not None else ()
        if server is not None:
            normalized = server.strip()
            statuses = tuple(item for item in statuses if item.name == normalized)
            if not statuses:
                raise ValueError(f"MCP Server '{normalized}' 不存在")

        entries: list[dict[str, Any]] = []
        for status in statuses:
            item: dict[str, Any] = {
                "name": status.name,
                "state": status.state.value,
                "tool_count": len(status.tool_names),
            }
            if include_tools:
                item["tools"] = list(status.tool_names)
            if status.error:
                item["error"] = status.error
            entries.append(item)

        return {
            "server_count": len(entries),
            "running_count": sum(
                1 for item in entries if item["state"] == "running"
            ),
            "configuration_error": self._configuration_error,
            "servers": entries,
        }


__all__ = ["MCP_STATUS_TOOL_NAME", "MCPStatusTool"]
