"""MCP 配置与运行状态模型。"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.types import ToolPermission

_SERVER_NAME_RE = re.compile(r"^[a-zA-Z0-9_]+$")


class MCPTransport(StrEnum):
    """OneAgent 当前支持的 MCP 传输类型。"""

    STDIO = "stdio"


class MCPServerState(StrEnum):
    """MCP Server 的当前连接状态。"""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    FAILED = "failed"


class MCPServerConfig(BaseModel):
    """一个 stdio MCP Server 的静态配置。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    transport: MCPTransport = MCPTransport.STDIO
    command: str = Field(min_length=1)
    args: tuple[str, ...] = ()
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None
    enabled: bool = True
    startup_timeout_seconds: float = Field(default=15.0, gt=0)
    call_timeout_seconds: float = Field(default=30.0, gt=0)
    permission: ToolPermission = ToolPermission.HUMAN_APPROVAL

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not _SERVER_NAME_RE.fullmatch(value):
            raise ValueError("name 只能包含字母、数字和下划线")
        return value


class MCPSettings(BaseModel):
    """MCP 配置文件的顶层结构。"""

    model_config = ConfigDict(extra="forbid")

    servers: tuple[MCPServerConfig, ...] = ()

    @field_validator("servers")
    @classmethod
    def reject_duplicate_names(
        cls,
        value: tuple[MCPServerConfig, ...],
    ) -> tuple[MCPServerConfig, ...]:
        names = [server.name for server in value]
        if len(names) != len(set(names)):
            raise ValueError("MCP Server 名称不能重复")
        return value


class MCPRemoteTool(BaseModel):
    """从 MCP Server 发现的原始工具描述。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )


class MCPServerStatus(BaseModel):
    """提供给 CLI 和测试的 MCP Server 状态快照。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    state: MCPServerState
    tool_names: tuple[str, ...] = ()
    error: str | None = None


__all__ = [
    "MCPRemoteTool",
    "MCPServerConfig",
    "MCPServerState",
    "MCPServerStatus",
    "MCPSettings",
    "MCPTransport",
]
