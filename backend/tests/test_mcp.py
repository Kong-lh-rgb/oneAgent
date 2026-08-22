from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent
from pydantic import SecretStr

from app.agent.runtime import AgentRuntime
from app.mcp import (
    MCPClientManager,
    MCPConfigurationError,
    MCPRemoteTool,
    MCPServerConfig,
    MCPServerState,
    MCPStatusTool,
    MCPToolCallError,
    StdioMCPClient,
    load_mcp_settings,
    mcp_tool_name,
    serialize_mcp_result,
)
from app.models.adapter import ModelAdapter
from app.models.config import ModelSettings, ProviderConfig
from app.models.registry import ModelAdapterRegistry
from app.models.types import (
    ApiStyle,
    Message,
    MessageRole,
    ModelRequest,
    ModelResponse,
    ToolCall,
    ToolPermission,
)
from app.tools import ToolExecutor, ToolRegistry


class FakeMCPClient:
    """不访问网络和子进程的 MCP 客户端替身。"""

    def __init__(
        self,
        config: MCPServerConfig,
        *,
        fail_start: bool = False,
        tools: tuple[MCPRemoteTool, ...] | None = None,
    ) -> None:
        self.config = config
        self.fail_start = fail_start
        self.tools = tools or (
            MCPRemoteTool(
                name="echo.text",
                description="返回参数",
                input_schema={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            ),
        )
        self.started = False
        self.closed = False
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def start(self) -> None:
        if self.fail_start:
            raise RuntimeError("无法启动")
        self.started = True

    async def list_tools(self) -> tuple[MCPRemoteTool, ...]:
        return self.tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        self.calls.append((name, arguments))
        return json.dumps(
            {"remote_name": name, "arguments": arguments},
            ensure_ascii=False,
        )

    async def close(self) -> None:
        self.closed = True


class FakeModelAdapter(ModelAdapter):
    """依次搜索工具、调用 MCP 工具并返回最终回答。"""

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if len(self.requests) == 1:
            message = Message(
                role=MessageRole.ASSISTANT,
                tool_calls=(
                    ToolCall(
                        id="tool-search-1",
                        name="tool_search",
                        arguments={"query": "echo 返回参数"},
                    ),
                ),
            )
        elif len(self.requests) == 2:
            message = Message(
                role=MessageRole.ASSISTANT,
                tool_calls=(
                    ToolCall(
                        id="mcp-runtime-1",
                        name="mcp__demo__echo_text",
                        arguments={"text": "runtime"},
                    ),
                ),
            )
        else:
            message = Message(role=MessageRole.ASSISTANT, content="MCP 调用完成")
        return ModelResponse(
            id=f"response-{len(self.requests)}",
            provider="fake",
            model="fake-model",
            message=message,
        )

    async def close(self) -> None:
        pass


class DirectDeferredCallAdapter(ModelAdapter):
    """模拟模型绕过目录直接猜测延迟工具名。"""

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self.calls = 0

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if self.calls == 1:
            message = Message(
                role=MessageRole.ASSISTANT,
                tool_calls=(
                    ToolCall(
                        id="guessed-mcp-call",
                        name="mcp__demo__echo_text",
                        arguments={"text": "bypass"},
                    ),
                ),
            )
        else:
            message = Message(role=MessageRole.ASSISTANT, content="已停止绕过")
        return ModelResponse(
            id=f"direct-{self.calls}",
            provider="fake",
            model="fake-model",
            message=message,
        )

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_manager_registers_mcp_tool_into_existing_execution_chain() -> None:
    config = MCPServerConfig(
        name="demo",
        command="unused",
        permission=ToolPermission.ALLOWED,
    )
    clients: list[FakeMCPClient] = []

    def factory(value: MCPServerConfig) -> FakeMCPClient:
        client = FakeMCPClient(value)
        clients.append(client)
        return client

    registry = ToolRegistry()
    manager = MCPClientManager((config,), client_factory=factory)

    statuses = await manager.start(registry)
    result = await ToolExecutor(registry).execute(
        ToolCall(
            id="mcp-1",
            name="mcp__demo__echo_text",
            arguments={"text": "你好"},
        )
    )

    assert statuses[0].state is MCPServerState.RUNNING
    assert statuses[0].tool_names == ("mcp__demo__echo_text",)
    assert registry.deferred_names() == ("mcp__demo__echo_text",)
    assert registry.model_definitions() == ()
    assert result.success is True
    assert json.loads(result.output or "{}") == {
        "remote_name": "echo.text",
        "arguments": {"text": "你好"},
    }
    await manager.close(registry)
    assert clients[0].closed is True
    assert registry.names() == ()


@pytest.mark.asyncio
async def test_agent_runtime_can_complete_an_mcp_tool_call() -> None:
    config = MCPServerConfig(
        name="demo",
        command="unused",
        permission=ToolPermission.ALLOWED,
    )
    tools = ToolRegistry()
    manager = MCPClientManager(
        (config,),
        client_factory=lambda value: FakeMCPClient(value),
    )
    await manager.start(tools)
    provider_config = ProviderConfig(
        provider="fake",
        model="fake-model",
        api_key=SecretStr("offline"),
        api_style=ApiStyle.CHAT_COMPLETIONS,
    )
    adapter = FakeModelAdapter(provider_config)
    models = ModelAdapterRegistry(ModelSettings(_env_file=None))
    models.register("fake", lambda _: adapter, config=provider_config)

    result = await AgentRuntime(models, tools, provider="fake").run("调用 MCP")

    assert result.content == "MCP 调用完成"
    assert len(result.tool_calls) == 2
    assert all(record.result.success for record in result.tool_calls)
    assert [tool.name for tool in adapter.requests[0].tools] == ["tool_search"]
    assert {tool.name for tool in adapter.requests[1].tools} == {
        "tool_search",
        "mcp__demo__echo_text",
    }
    tool_message = adapter.requests[2].messages[-1]
    assert tool_message.role is MessageRole.TOOL
    assert "runtime" in (tool_message.content or "")
    await manager.close(tools)


@pytest.mark.asyncio
async def test_runtime_rejects_deferred_tool_before_catalog_activation() -> None:
    config = MCPServerConfig(
        name="demo",
        command="unused",
        permission=ToolPermission.ALLOWED,
    )
    tools = ToolRegistry()
    client = FakeMCPClient(config)
    manager = MCPClientManager((config,), client_factory=lambda value: client)
    await manager.start(tools)
    provider_config = ProviderConfig(
        provider="fake",
        model="fake-model",
        api_key=SecretStr("offline"),
        api_style=ApiStyle.CHAT_COMPLETIONS,
    )
    adapter = DirectDeferredCallAdapter(provider_config)
    models = ModelAdapterRegistry(ModelSettings(_env_file=None))
    models.register("fake", lambda _: adapter, config=provider_config)

    result = await AgentRuntime(models, tools, provider="fake").run("绕过目录")

    assert result.tool_calls[0].result.success is False
    assert "tool_search" in (result.tool_calls[0].result.error or "")
    assert client.calls == []
    await manager.close(tools)


@pytest.mark.asyncio
async def test_one_failed_server_does_not_block_other_servers() -> None:
    configs = (
        MCPServerConfig(name="broken", command="unused"),
        MCPServerConfig(name="healthy", command="unused"),
    )

    def factory(config: MCPServerConfig) -> FakeMCPClient:
        return FakeMCPClient(config, fail_start=config.name == "broken")

    registry = ToolRegistry()
    manager = MCPClientManager(configs, client_factory=factory)

    statuses = await manager.start(registry)

    assert statuses[0].state is MCPServerState.FAILED
    assert "无法启动" in (statuses[0].error or "")
    assert statuses[1].state is MCPServerState.RUNNING
    assert registry.names() == ("mcp__healthy__echo_text",)
    await manager.close(registry)


@pytest.mark.asyncio
async def test_mcp_status_tool_reads_live_manager_without_starting_servers() -> None:
    configs = (
        MCPServerConfig(name="broken", command="unused"),
        MCPServerConfig(name="healthy", command="unused"),
    )
    clients: list[FakeMCPClient] = []

    def factory(config: MCPServerConfig) -> FakeMCPClient:
        client = FakeMCPClient(
            config,
            fail_start=config.name == "broken",
        )
        clients.append(client)
        return client

    registry = ToolRegistry()
    manager = MCPClientManager(configs, client_factory=factory)
    await manager.start(registry)
    starts_before = [client.started for client in clients]
    tool = MCPStatusTool(manager)

    result = await tool.execute({})

    assert result["server_count"] == 2
    assert result["running_count"] == 1
    assert result["servers"][0]["state"] == "failed"
    assert result["servers"][1]["tools"] == ["mcp__healthy__echo_text"]
    assert [client.started for client in clients] == starts_before
    assert tool.definition.permission is ToolPermission.ALLOWED
    await manager.close(registry)


@pytest.mark.asyncio
async def test_mcp_status_tool_filters_server_and_can_hide_tool_names() -> None:
    config = MCPServerConfig(name="demo", command="unused")
    registry = ToolRegistry()
    manager = MCPClientManager(
        (config,),
        client_factory=lambda value: FakeMCPClient(value),
    )
    await manager.start(registry)

    result = await MCPStatusTool(manager).execute(
        {"server": "demo", "include_tools": False}
    )

    assert result["servers"] == [
        {"name": "demo", "state": "running", "tool_count": 1}
    ]
    with pytest.raises(ValueError, match="不存在"):
        await MCPStatusTool(manager).execute({"server": "missing"})
    await manager.close(registry)


@pytest.mark.asyncio
async def test_registration_collision_rolls_back_server_tools() -> None:
    tools = (
        MCPRemoteTool(name="same.name"),
        MCPRemoteTool(name="same-name"),
    )
    config = MCPServerConfig(name="demo", command="unused")
    registry = ToolRegistry()
    manager = MCPClientManager(
        (config,),
        client_factory=lambda value: FakeMCPClient(value, tools=tools),
    )

    statuses = await manager.start(registry)

    assert statuses[0].state is MCPServerState.FAILED
    assert "发生冲突" in (statuses[0].error or "")
    assert registry.names() == ()


def test_mcp_tool_name_uses_stable_namespace() -> None:
    assert mcp_tool_name("files", "read.file-v2") == "mcp__files__read_file_v2"
    with pytest.raises(MCPConfigurationError, match="无法注册"):
        mcp_tool_name("files", "---")


@pytest.mark.asyncio
async def test_load_settings_handles_missing_and_invalid_config(tmp_path: Path) -> None:
    assert (await load_mcp_settings(tmp_path / "missing.json")).servers == ()
    invalid = tmp_path / "mcp.json"
    invalid.write_text('{"servers":[{"name":"bad name"}]}', encoding="utf-8")

    with pytest.raises(MCPConfigurationError, match="无法加载"):
        await load_mcp_settings(invalid)


@pytest.mark.asyncio
async def test_missing_environment_reference_fails_only_that_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VESTA_MISSING_MCP_KEY", raising=False)
    config = MCPServerConfig(
        name="missing_env",
        command=sys.executable,
        args=("-c", "pass"),
        env={"API_KEY": "${VESTA_MISSING_MCP_KEY}"},
    )
    registry = ToolRegistry()
    manager = MCPClientManager((config,))

    statuses = await manager.start(registry)

    assert statuses[0].state is MCPServerState.FAILED
    assert "VESTA_MISSING_MCP_KEY" in (statuses[0].error or "")


def test_serialize_mcp_result_preserves_text_and_structured_content() -> None:
    plain = CallToolResult(content=[TextContent(type="text", text="hello")])
    structured = CallToolResult(
        content=[TextContent(type="text", text="hello")],
        structuredContent={"count": 1},
    )

    assert serialize_mcp_result(plain) == "hello"
    assert json.loads(serialize_mcp_result(structured)) == {
        "content": [{"type": "text", "text": "hello"}],
        "structured_content": {"count": 1},
    }


def _stdio_config(*, call_timeout: float = 2.0) -> MCPServerConfig:
    server_path = Path(__file__).parent / "fixtures" / "fake_mcp_server.py"
    return MCPServerConfig(
        name="stdio_test",
        command=sys.executable,
        args=(str(server_path),),
        startup_timeout_seconds=5,
        call_timeout_seconds=call_timeout,
        permission=ToolPermission.ALLOWED,
    )


@pytest.mark.asyncio
async def test_stdio_client_discovers_and_calls_real_fake_server() -> None:
    client = StdioMCPClient(_stdio_config())
    await client.start()
    try:
        tools = await client.list_tools()
        assert {tool.name for tool in tools} == {"echo", "fail", "slow"}
        output = json.loads(await client.call_tool("echo", {"text": "你好 MCP"}))
        assert output["content"] == [{"type": "text", "text": "你好 MCP"}]
        assert output["structured_content"] == {"result": "你好 MCP"}
        with pytest.raises(MCPToolCallError, match="fake boom"):
            await client.call_tool("fail", {})
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_stdio_client_call_timeout() -> None:
    client = StdioMCPClient(_stdio_config(call_timeout=0.05))
    await client.start()
    try:
        with pytest.raises(MCPToolCallError, match="TimeoutError"):
            await client.call_tool("slow", {"delay": 0.5})
    finally:
        await client.close()
