"""MCP Server 生命周期、工具发现与注册管理。"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from app.tools.registry import ToolRegistry

from .client import MCPClientProtocol, StdioMCPClient
from .errors import MCPConfigurationError
from .models import (
    MCPServerConfig,
    MCPServerState,
    MCPServerStatus,
)
from .tool import MCPToolAdapter

MCPClientFactory = Callable[[MCPServerConfig], MCPClientProtocol]
_INVALID_TOOL_NAME = re.compile(r"[^a-zA-Z0-9_]+")


class MCPClientManager:
    """隔离管理多个 MCP Server；单个失败不影响其他服务器。"""

    def __init__(
        self,
        configs: Sequence[MCPServerConfig],
        *,
        client_factory: MCPClientFactory = StdioMCPClient,
    ) -> None:
        names = [config.name for config in configs]
        if len(names) != len(set(names)):
            raise MCPConfigurationError("MCP Server 名称不能重复")
        self._configs = tuple(configs)
        self._client_factory = client_factory
        self._clients: dict[str, MCPClientProtocol] = {}
        self._registered_names: dict[str, tuple[str, ...]] = {}
        self._states = {
            config.name: MCPServerStatus(
                name=config.name,
                state=MCPServerState.STOPPED,
            )
            for config in self._configs
        }

    async def start(self, registry: ToolRegistry) -> tuple[MCPServerStatus, ...]:
        """启动全部启用服务器并将发现的工具注册到现有 Registry。"""

        for config in self._configs:
            if not config.enabled:
                continue
            await self._start_server(config, registry)
        return self.statuses()

    async def _start_server(
        self,
        config: MCPServerConfig,
        registry: ToolRegistry,
    ) -> None:
        if config.name in self._clients:
            return
        self._set_status(config.name, MCPServerState.STARTING)
        client = self._client_factory(config)
        registered: list[str] = []
        try:
            await client.start()
            remote_tools = await client.list_tools()
            adapters = _build_adapters(config, client, remote_tools)
            for adapter in adapters:
                # MCP 工具默认延迟暴露，避免每一步都发送全部远端 Schema。
                registry.register(adapter, deferred=True)
                registered.append(adapter.definition.name)
        except Exception as exc:
            for name in reversed(registered):
                registry.unregister(name)
            try:
                await client.close()
            except Exception:
                pass
            self._set_status(
                config.name,
                MCPServerState.FAILED,
                error=f"{type(exc).__name__}: {exc}",
            )
            return
        self._clients[config.name] = client
        self._registered_names[config.name] = tuple(registered)
        self._set_status(
            config.name,
            MCPServerState.RUNNING,
            tool_names=tuple(registered),
        )

    async def close(self, registry: ToolRegistry | None = None) -> None:
        """关闭全部连接；传入 Registry 时同时注销 MCP 工具。"""

        for server_name, client in reversed(tuple(self._clients.items())):
            if registry is not None:
                for name in self._registered_names.get(server_name, ()):
                    try:
                        registry.unregister(name)
                    except KeyError:
                        pass
            try:
                await client.close()
            except Exception as exc:
                self._set_status(
                    server_name,
                    MCPServerState.FAILED,
                    error=f"关闭失败: {type(exc).__name__}: {exc}",
                )
                continue
            self._set_status(server_name, MCPServerState.STOPPED)
        self._clients.clear()
        self._registered_names.clear()

    def statuses(self) -> tuple[MCPServerStatus, ...]:
        """按配置顺序返回不可变状态快照。"""

        return tuple(self._states[config.name] for config in self._configs)

    def _set_status(
        self,
        name: str,
        state: MCPServerState,
        *,
        tool_names: tuple[str, ...] = (),
        error: str | None = None,
    ) -> None:
        self._states[name] = MCPServerStatus(
            name=name,
            state=state,
            tool_names=tool_names,
            error=error,
        )


def mcp_tool_name(server_name: str, remote_name: str) -> str:
    """生成模型可见的稳定命名空间，兼容工具名字符约束。"""

    normalized = _INVALID_TOOL_NAME.sub("_", remote_name).strip("_")
    if not normalized:
        raise MCPConfigurationError(
            f"MCP Server '{server_name}' 返回了无法注册的工具名 {remote_name!r}"
        )
    return f"mcp__{server_name}__{normalized}"


def _build_adapters(
    config: MCPServerConfig,
    client: MCPClientProtocol,
    remote_tools: Sequence,
) -> tuple[MCPToolAdapter, ...]:
    adapters: list[MCPToolAdapter] = []
    seen: set[str] = set()
    for remote_tool in remote_tools:
        registered_name = mcp_tool_name(config.name, remote_tool.name)
        if registered_name in seen:
            raise MCPConfigurationError(
                f"MCP Server '{config.name}' 的工具名规范化后发生冲突: "
                f"{registered_name}"
            )
        seen.add(registered_name)
        adapters.append(
            MCPToolAdapter(
                server_name=config.name,
                registered_name=registered_name,
                remote_tool=remote_tool,
                client=client,
                permission=config.permission,
            )
        )
    return tuple(adapters)


__all__ = ["MCPClientFactory", "MCPClientManager", "mcp_tool_name"]
