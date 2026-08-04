"""内置工具。"""

from pathlib import Path

from ..registry import ToolRegistry
from ..search import SearchSettings
from .http_request import HttpRequestTool
from .list_files import ListFilesTool
from .read_file import ReadFileTool
from .shell import ShellCommandTool
from .web_search import WebSearchTool
from .write_file import WriteFileTool


def build_builtin_tool_registry(
    workspace_root: str | Path | None = None,
    *,
    search_settings: SearchSettings | None = None,
) -> ToolRegistry:
    """创建注册了全部内置工具的工具注册表。"""

    registry = ToolRegistry()
    registry.register(ListFilesTool(workspace_root))
    registry.register(ReadFileTool(workspace_root))
    registry.register(WriteFileTool(workspace_root))
    registry.register(ShellCommandTool(workspace_root))
    registry.register(HttpRequestTool())
    registry.register(WebSearchTool(settings=search_settings))
    return registry

__all__ = [
    "HttpRequestTool",
    "ListFilesTool",
    "ReadFileTool",
    "ShellCommandTool",
    "WebSearchTool",
    "WriteFileTool",
    "build_builtin_tool_registry",
]
