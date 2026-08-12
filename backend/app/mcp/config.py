"""加载本地 MCP Server 配置。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from pydantic import ValidationError

from .errors import MCPConfigurationError
from .models import MCPSettings

DEFAULT_MCP_CONFIG_PATH = (
    Path(__file__).resolve().parents[2] / ".oneagent" / "mcp.json"
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


__all__ = ["DEFAULT_MCP_CONFIG_PATH", "load_mcp_settings"]
