"""MCP Client 集成测试使用的本地 stdio Server。"""

from __future__ import annotations

import asyncio

from mcp.server.fastmcp import FastMCP

server = FastMCP("oneagent-test")


@server.tool()
def echo(text: str) -> str:
    """原样返回文本。"""

    return text


@server.tool()
async def slow(delay: float) -> str:
    """延迟返回，用于验证调用超时。"""

    await asyncio.sleep(delay)
    return "done"


@server.tool()
def fail() -> str:
    """主动抛出异常。"""

    raise RuntimeError("fake boom")


if __name__ == "__main__":
    server.run(transport="stdio")
