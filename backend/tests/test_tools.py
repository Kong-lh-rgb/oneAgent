from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from app.models.types import ToolCall, ToolDefinition
from app.tools import (
    BaseTool,
    CurrentTimeTool,
    ListFilesTool,
    ReadFileTool,
    ToolExecutor,
    ToolRegistry,
    WriteFileTool,
)
from app.tools.catalog import ToolCatalog, ToolSearchTool


class EchoTool(BaseTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(name="echo")

    async def execute(self, arguments: dict[str, Any]) -> Any:
        return arguments


class StubDefinitionTool(BaseTool):
    def __init__(self, definition: ToolDefinition) -> None:
        self._definition = definition

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, arguments: dict[str, Any]) -> Any:
        return arguments


class FailingTool(BaseTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(name="failing")

    async def execute(self, arguments: dict[str, Any]) -> Any:
        raise RuntimeError("boom")


class SlowTool(BaseTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(name="slow")

    async def execute(self, arguments: dict[str, Any]) -> Any:
        await asyncio.sleep(1)
        return "too late"


class LargeOutputTool(BaseTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(name="large_output")

    async def execute(self, arguments: dict[str, Any]) -> Any:
        return "x" * 25_000


def test_tool_registration_and_definitions() -> None:
    registry = ToolRegistry()
    tool = EchoTool()

    registry.register(tool)

    assert registry.get("echo") is tool
    assert [definition.name for definition in registry.definitions()] == ["echo"]


def test_duplicate_tool_registration_is_rejected() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())

    with pytest.raises(ValueError, match="already registered"):
        registry.register(EchoTool())


def test_model_definitions_are_stable_across_registration_order() -> None:
    first = ToolRegistry()
    first.register(StubDefinitionTool(ToolDefinition(name="zeta")))
    first.register(StubDefinitionTool(ToolDefinition(name="alpha")))
    second = ToolRegistry()
    second.register(StubDefinitionTool(ToolDefinition(name="alpha")))
    second.register(StubDefinitionTool(ToolDefinition(name="zeta")))

    assert first.model_definitions() == second.model_definitions()
    assert [item.name for item in first.model_definitions()] == ["alpha", "zeta"]


def test_tool_catalog_tracks_deferred_registry_changes_automatically() -> None:
    registry = ToolRegistry()
    weather = StubDefinitionTool(
        ToolDefinition(
            name="mcp__weather__forecast",
            description="Get weather forecast by city and date",
            parameters={
                "type": "object",
                "properties": {"city": {"type": "string"}},
            },
        )
    )
    registry.register(weather, deferred=True)
    catalog = ToolCatalog(registry)

    assert [match.name for match in catalog.search("weather city")] == [
        "mcp__weather__forecast"
    ]
    registry.unregister("mcp__weather__forecast")
    assert catalog.search("weather city") == ()


@pytest.mark.asyncio
async def test_tool_search_returns_compact_matching_definitions() -> None:
    registry = ToolRegistry()
    registry.register(
        StubDefinitionTool(
            ToolDefinition(
                name="mcp__maps__search_places",
                description="Search places and points of interest on a map",
            )
        ),
        deferred=True,
    )
    registry.register(EchoTool())

    payload = json.loads(
        await ToolSearchTool(registry).execute({"query": "map places"})
    )

    assert payload["count"] == 1
    assert payload["tools"][0]["name"] == "mcp__maps__search_places"
    assert [item.name for item in registry.model_definitions()] == ["echo"]
    assert {
        item.name
        for item in registry.model_definitions(
            activated_names={"mcp__maps__search_places"}
        )
    } == {"echo", "mcp__maps__search_places"}


@pytest.mark.asyncio
async def test_current_time_tool_returns_local_time_on_demand() -> None:
    output = await CurrentTimeTool().execute({})

    assert output["date"] in output["datetime"]
    assert output["time"] in output["datetime"]
    assert output["timezone"]
    assert output["utc_offset"]
    assert isinstance(output["unix_timestamp"], int)


@pytest.mark.asyncio
async def test_current_time_tool_supports_iana_timezone() -> None:
    output = await CurrentTimeTool().execute({"timezone": "Asia/Shanghai"})

    assert output["timezone"] == "Asia/Shanghai"
    assert output["utc_offset"] == "+08:00"


@pytest.mark.asyncio
async def test_current_time_tool_rejects_unknown_timezone() -> None:
    with pytest.raises(ValueError, match="未知 IANA 时区"):
        await CurrentTimeTool().execute({"timezone": "Mars/Olympus"})


@pytest.mark.asyncio
async def test_write_file_creates_parent_and_returns_metadata(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    registry.register(WriteFileTool(tmp_path))
    executor = ToolExecutor(registry)

    result = await executor.execute(
        ToolCall(
            id="write-1",
            name="write_file",
            arguments={"path": "notes/你好.txt", "content": "本地工具"},
        )
    )

    assert result.success is True
    assert result.error is None
    assert json.loads(result.output or "{}") == {
        "path": "notes/你好.txt",
        "characters": 4,
    }
    assert (tmp_path / "notes/你好.txt").read_text(encoding="utf-8") == "本地工具"


@pytest.mark.asyncio
async def test_read_file_returns_utf8_text(tmp_path: Path) -> None:
    target = tmp_path / "文档.txt"
    target.write_text("你好，Vesta", encoding="utf-8")
    registry = ToolRegistry()
    registry.register(ReadFileTool(tmp_path))
    executor = ToolExecutor(registry)

    result = await executor.execute(
        ToolCall(
            id="read-1",
            name="read_file",
            arguments='{"path":"文档.txt"}',
        )
    )

    assert result.success is True
    assert result.output == "你好，Vesta"
    assert result.duration_ms >= 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        (ReadFileTool, {"path": "../secret.txt"}),
        (
            WriteFileTool,
            {"path": "../escaped.txt", "content": "must not be written"},
        ),
    ],
)
async def test_path_traversal_is_rejected(
    tmp_path: Path,
    tool: type[BaseTool],
    arguments: dict[str, Any],
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    registry = ToolRegistry()
    registry.register(tool(workspace))  # type: ignore[call-arg]
    executor = ToolExecutor(registry)

    result = await executor.execute(
        ToolCall(
            id="traversal-1", name=tool(workspace).definition.name, arguments=arguments
        )  # type: ignore[call-arg]
    )

    assert result.success is False
    assert result.output is None
    assert result.error is not None
    assert "escapes the workspace" in result.error
    assert not (tmp_path / "escaped.txt").exists()


@pytest.mark.asyncio
async def test_list_files_supports_subdirectory_and_limit(tmp_path: Path) -> None:
    subdirectory = tmp_path / "items"
    subdirectory.mkdir()
    for index in range(205):
        (subdirectory / f"{index:03}.txt").write_text("x", encoding="utf-8")
    registry = ToolRegistry()
    registry.register(ListFilesTool(tmp_path))
    executor = ToolExecutor(registry)

    result = await executor.execute(
        ToolCall(
            id="list-1",
            name="list_files",
            arguments={"directory": "items"},
        )
    )

    output = json.loads(result.output or "{}")
    assert result.success is True
    assert output["count"] == 200
    assert len(output["files"]) == 200
    assert output["files"][0] == "items/000.txt"
    assert output["truncated"] is True


@pytest.mark.asyncio
async def test_unknown_tool_returns_failure() -> None:
    result = await ToolExecutor(ToolRegistry()).execute(
        ToolCall(id="missing-1", name="missing", arguments={})
    )

    assert result.success is False
    assert result.error == "Tool not found: missing"


@pytest.mark.asyncio
async def test_tool_exception_returns_failure() -> None:
    registry = ToolRegistry()
    registry.register(FailingTool())

    result = await ToolExecutor(registry).execute(
        ToolCall(id="fail-1", name="failing", arguments={})
    )

    assert result.success is False
    assert result.error == "Tool execution failed: RuntimeError: boom"
    assert result.duration_ms >= 0


@pytest.mark.asyncio
async def test_tool_timeout_returns_failure() -> None:
    registry = ToolRegistry()
    registry.register(SlowTool())

    result = await ToolExecutor(registry, timeout_seconds=0.01).execute(
        ToolCall(id="slow-1", name="slow", arguments={})
    )

    assert result.success is False
    assert result.error == "Tool timed out after 0.01 seconds."
    assert result.duration_ms >= 10


@pytest.mark.asyncio
async def test_invalid_json_arguments_return_failure() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())

    result = await ToolExecutor(registry).execute(
        ToolCall(id="echo-1", name="echo", arguments="{invalid")
    )

    assert result.success is False
    assert result.error is not None
    assert result.error.startswith("Invalid arguments:")


@pytest.mark.asyncio
async def test_tool_output_is_limited_to_20000_characters() -> None:
    registry = ToolRegistry()
    registry.register(LargeOutputTool())

    result = await ToolExecutor(registry).execute(
        ToolCall(id="large-1", name="large_output", arguments={})
    )

    assert result.success is True
    assert result.output is not None
    assert len(result.output) == 20_000


def test_tool_output_limit_cannot_exceed_20000_characters() -> None:
    with pytest.raises(ValueError, match="cannot exceed 20000"):
        ToolExecutor(ToolRegistry(), max_output_chars=20_001)
