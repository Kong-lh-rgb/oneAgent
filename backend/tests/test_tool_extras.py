"""新增内置工具（shell / http_request / web_search）测试。"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.models.types import ToolCall, ToolPermission
from app.tools import (
    AutoApproveGate,
    HttpRequestTool,
    ShellCommandTool,
    ToolExecutor,
    ToolRegistry,
    WebSearchTool,
)


def executor_with(*tools: Any, gate: AutoApproveGate | None = None) -> ToolExecutor:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return ToolExecutor(registry, approval_gate=gate or AutoApproveGate())


def test_new_tool_permissions_match_operation_risk() -> None:
    assert ShellCommandTool().definition.permission is ToolPermission.HUMAN_APPROVAL
    assert HttpRequestTool().definition.permission is ToolPermission.HUMAN_APPROVAL
    assert WebSearchTool().definition.permission is ToolPermission.ALLOWED


@pytest.mark.asyncio
async def test_shell_blocked_without_approval_gate(tmp_path) -> None:
    registry = ToolRegistry()
    registry.register(ShellCommandTool(tmp_path))

    result = await ToolExecutor(registry).execute(
        ToolCall(
            id="sh-0",
            name="run_shell_command",
            arguments={"command": "echo nope"},
        )
    )

    assert result.success is False
    assert "human approval" in (result.error or "")


@pytest.mark.asyncio
async def test_shell_command_runs_and_captures_output(tmp_path) -> None:
    executor = executor_with(ShellCommandTool(tmp_path))

    result = await executor.execute(
        ToolCall(
            id="sh-1",
            name="run_shell_command",
            arguments={"command": "echo hello vesta"},
        )
    )

    assert result.success is True
    output = json.loads(result.output or "{}")
    assert output["exit_code"] == 0
    assert "hello vesta" in output["stdout"]


@pytest.mark.asyncio
async def test_shell_command_reports_nonzero_exit(tmp_path) -> None:
    executor = executor_with(ShellCommandTool(tmp_path))

    result = await executor.execute(
        ToolCall(
            id="sh-2",
            name="run_shell_command",
            arguments={"command": "exit 3"},
        )
    )

    assert result.success is True
    output = json.loads(result.output or "{}")
    assert output["exit_code"] == 3


@pytest.mark.asyncio
async def test_shell_command_timeout_terminates(tmp_path) -> None:
    executor = executor_with(ShellCommandTool(tmp_path))

    result = await executor.execute(
        ToolCall(
            id="sh-3",
            name="run_shell_command",
            arguments={"command": "sleep 30", "timeout_seconds": 0.2},
        )
    )

    assert result.success is True
    output = json.loads(result.output or "{}")
    assert output["timed_out"] is True


@pytest.mark.asyncio
async def test_shell_command_uses_working_directory(tmp_path) -> None:
    executor = executor_with(ShellCommandTool(tmp_path))

    result = await executor.execute(
        ToolCall(
            id="sh-4",
            name="run_shell_command",
            arguments={"command": "pwd", "working_directory": "."},
        )
    )

    output = json.loads(result.output or "{}")
    assert output["exit_code"] == 0
    assert output["working_directory"] == str(tmp_path.resolve())


@pytest.mark.asyncio
async def test_http_request_get_returns_body() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            text="<html>vesta</html>",
            headers={"content-type": "text/html; charset=utf-8"},
        )
    )
    client = httpx.AsyncClient(transport=transport)
    executor = executor_with(HttpRequestTool(client=client))

    result = await executor.execute(
        ToolCall(
            id="http-1",
            name="http_request",
            arguments={"url": "https://example.com/", "method": "GET"},
        )
    )

    assert result.success is True
    output = json.loads(result.output or "{}")
    assert output["status_code"] == 200
    assert "vesta" in output["text"]


@pytest.mark.asyncio
async def test_http_request_post_sends_body() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["content"] = request.content.decode()
        return httpx.Response(201, text="created")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    executor = executor_with(HttpRequestTool(client=client))

    result = await executor.execute(
        ToolCall(
            id="http-2",
            name="http_request",
            arguments={
                "url": "https://example.com/items",
                "method": "POST",
                "body": '{"name":"x"}',
                "headers": {"content-type": "application/json"},
            },
        )
    )

    assert result.success is True
    assert json.loads(result.output or "{}")["status_code"] == 201
    assert captured["method"] == "POST"
    assert captured["content"] == '{"name":"x"}'


@pytest.mark.asyncio
async def test_http_request_blocks_private_addresses() -> None:
    executor = executor_with(HttpRequestTool())

    result = await executor.execute(
        ToolCall(
            id="http-3",
            name="http_request",
            arguments={"url": "http://127.0.0.1:9999/"},
        )
    )

    assert result.success is False
    assert "SSRF" in (result.error or "")


@pytest.mark.asyncio
async def test_http_request_with_injected_client_bypasses_ssrf() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="ok"))
    )
    executor = executor_with(HttpRequestTool(client=client))

    result = await executor.execute(
        ToolCall(
            id="http-4",
            name="http_request",
            arguments={"url": "http://127.0.0.1:9/"},
        )
    )

    assert result.success is True
    assert json.loads(result.output or "{}")["text"] == "ok"
