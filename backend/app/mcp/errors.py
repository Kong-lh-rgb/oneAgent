"""MCP 客户端错误。"""


class MCPError(RuntimeError):
    """OneAgent 可识别的 MCP 基础错误。"""


class MCPConfigurationError(MCPError):
    """MCP 配置无效。"""


class MCPConnectionError(MCPError):
    """MCP Server 启动、初始化或连接失败。"""


class MCPToolDiscoveryError(MCPError):
    """无法从 MCP Server 获取工具列表。"""


class MCPToolCallError(MCPError):
    """MCP 工具调用失败。"""


__all__ = [
    "MCPConfigurationError",
    "MCPConnectionError",
    "MCPError",
    "MCPToolCallError",
    "MCPToolDiscoveryError",
]
