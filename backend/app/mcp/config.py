"""加载本地 MCP Server 配置。"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from .errors import MCPConfigurationError
from .models import MCPServerConfig, MCPSettings

DEFAULT_MCP_CONFIG_PATH = (
    Path(__file__).resolve().parents[2] / ".vesta" / "mcp.json"
)


async def load_mcp_settings(
    path: str | Path = DEFAULT_MCP_CONFIG_PATH,
) -> MCPSettings:
    """读取 MCP JSON 配置；文件不存在表示尚未配置服务器。"""

    config_path = Path(path).expanduser().resolve()
    if not config_path.exists():
        return MCPSettings()
    try:
        raw = await asyncio.to_thread(config_path.read_text, encoding="utf-8")
        payload = json.loads(raw)
        return MCPSettings.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise MCPConfigurationError(
            f"无法加载 MCP 配置 {config_path}: {type(exc).__name__}: {exc}"
        ) from exc


class MCPConfigurationStore:
    """MCP JSON 配置存储：统一校验、去重并原子写入。"""

    def __init__(self, path: str | Path = DEFAULT_MCP_CONFIG_PATH) -> None:
        self.path = Path(path).expanduser().resolve()
        self._lock = asyncio.Lock()
        self._restart_required: set[str] = set()

    async def load(self) -> MCPSettings:
        """读取当前配置。"""

        return await load_mcp_settings(self.path)

    async def add(self, server: MCPServerConfig) -> MCPSettings:
        """添加一个 Server；名称重复时拒绝且不修改原文件。"""

        return await self.add_many((server,))

    async def add_many(
        self,
        servers: tuple[MCPServerConfig, ...],
    ) -> MCPSettings:
        """原子添加多个 Server；任一重名时整批拒绝。"""

        async with self._lock:
            current = await self.load()
            incoming_names = [server.name for server in servers]
            if len(incoming_names) != len(set(incoming_names)):
                raise ValueError("duplicate MCP Server names in import")
            existing_names = {item.name for item in current.servers}
            duplicate = next(
                (name for name in incoming_names if name in existing_names),
                None,
            )
            if duplicate is not None:
                raise ValueError(f"MCP Server '{duplicate}' already exists")
            updated = MCPSettings(servers=(*current.servers, *servers))
            await asyncio.to_thread(self._write, updated)
            self._restart_required.update(incoming_names)
            return updated

    async def set_enabled(self, name: str, *, enabled: bool) -> MCPServerConfig:
        """修改 Server enabled；写入后等待 Host 重启生效。"""

        async with self._lock:
            current = await self.load()
            found = next((item for item in current.servers if item.name == name), None)
            if found is None:
                raise KeyError(f"MCP Server '{name}' not found")
            updated_server = found.model_copy(update={"enabled": enabled})
            updated = MCPSettings(
                servers=tuple(
                    updated_server if item.name == name else item
                    for item in current.servers
                )
            )
            await asyncio.to_thread(self._write, updated)
            self._restart_required.add(name)
            return updated_server

    async def delete(self, name: str) -> None:
        """从 JSON 删除 Server；当前进程中的连接仍在重启时统一收口。"""

        async with self._lock:
            current = await self.load()
            if not any(item.name == name for item in current.servers):
                raise KeyError(f"MCP Server '{name}' not found")
            updated = MCPSettings(
                servers=tuple(item for item in current.servers if item.name != name)
            )
            await asyncio.to_thread(self._write, updated)
            self._restart_required.add(name)

    def restart_required(self, name: str) -> bool:
        """本进程启动后该 Server 的持久配置是否发生过变化。"""

        return name in self._restart_required

    @property
    def has_pending_changes(self) -> bool:
        """当前 Host 启动后是否写入过任何尚未应用的 MCP 变更。"""

        return bool(self._restart_required)

    def _write(self, settings: MCPSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            settings.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        ) + "\n"
        temporary = self.path.with_name(
            f".{self.path.name}.{uuid4().hex}.tmp"
        )
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                temporary.unlink()


__all__ = [
    "DEFAULT_MCP_CONFIG_PATH",
    "MCPConfigurationStore",
    "load_mcp_settings",
]
