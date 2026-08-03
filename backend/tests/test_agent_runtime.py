from __future__ import annotations

import json
from collections.abc import Sequence

import pytest
from pydantic import SecretStr

from app.agent.runtime import AgentRuntime
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
    ToolDefinition,
)
from app.tools.base import BaseTool
from app.tools.builtin.read_file import ReadFileTool
from app.tools.builtin.write_file import WriteFileTool
from app.tools.registry import ToolRegistry


class FakeModelAdapter(ModelAdapter):
    def __init__(
        self,
        config: ProviderConfig,
        responses: Sequence[ModelResponse | Exception],
    ) -> None:
        super().__init__(config)
        self.responses = list(responses)
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def close(self) -> None:
        pass


class CountingTool(BaseTool):
    definition = ToolDefinition(
        name="count",
        description="Count executions",
        parameters={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
        },
    )

    def __init__(self) -> None:
        self.executions = 0

    async def execute(self, arguments: dict[str, object]) -> str:
        self.executions += 1
        return str(arguments["value"])


def model_response(
    *,
    content: str | None = None,
    tool_calls: tuple[ToolCall, ...] = (),
) -> ModelResponse:
    return ModelResponse(
        id="fake-response",
        provider="fake",
        model="fake-model",
        message=Message(
            role=MessageRole.ASSISTANT,
            content=content,
            tool_calls=tool_calls,
        ),
    )


def fake_registry(
    responses: Sequence[ModelResponse | Exception],
) -> tuple[ModelAdapterRegistry, FakeModelAdapter]:
    config = ProviderConfig(
        provider="fake",
        model="fake-model",
        api_key=SecretStr("offline-test-key"),
        api_style=ApiStyle.CHAT_COMPLETIONS,
    )
    adapter = FakeModelAdapter(config, responses)
    registry = ModelAdapterRegistry(ModelSettings(_env_file=None))
    registry.register("fake", lambda _: adapter, config=config)
    return registry, adapter


@pytest.mark.asyncio
async def test_runtime_reads_then_writes_and_returns_final_text(tmp_path) -> None:
    (tmp_path / "input.txt").write_text(
        "OneAgent 可以调用本地工具完成文件任务。",
        encoding="utf-8",
    )

    registry, adapter = fake_registry(
        [
            model_response(
                tool_calls=(
                    ToolCall(
                        id="read-1",
                        name="read_file",
                        arguments={"path": "input.txt"},
                    ),
                )
            ),
            model_response(
                tool_calls=(
                    ToolCall(
                        id="write-1",
                        name="write_file",
                        arguments={
                            "path": "output.md",
                            "content": "# 摘要\nOneAgent 能调用本地文件工具。",
                        },
                    ),
                )
            ),
            model_response(content="摘要已写入 output.md"),
        ]
    )
    tools = ToolRegistry()
    tools.register(ReadFileTool(tmp_path))
    tools.register(WriteFileTool(tmp_path))

    result = await AgentRuntime(registry, tools, provider="fake").run(
        "读取 input.txt，生成摘要并写入 output.md"
    )

    assert result.content == "摘要已写入 output.md"
    assert (tmp_path / "output.md").read_text(encoding="utf-8") == (
        "# 摘要\nOneAgent 能调用本地文件工具。"
    )
    assert len(adapter.requests) == 3
    assert {tool.name for tool in adapter.requests[0].tools} == {
        "read_file",
        "write_file",
    }

    read_result_message = adapter.requests[1].messages[-1]
    assert read_result_message.role == MessageRole.TOOL
    assert read_result_message.tool_call_id == "read-1"
    read_result = json.loads(read_result_message.content or "{}")
    assert read_result["success"] is True
    assert "OneAgent 可以调用本地工具" in read_result["output"]

    write_result_message = adapter.requests[2].messages[-1]
    assert write_result_message.tool_call_id == "write-1"
    assert json.loads(write_result_message.content or "{}")["success"] is True


@pytest.mark.asyncio
async def test_model_error_returns_assistant_message() -> None:
    registry, _ = fake_registry([RuntimeError("model unavailable")])

    result = await AgentRuntime(
        registry,
        ToolRegistry(),
        provider="fake",
    ).run("hello")

    assert result.role == MessageRole.ASSISTANT
    assert "model invocation failed" in (result.content or "")
    assert "model unavailable" in (result.content or "")


@pytest.mark.asyncio
async def test_tool_error_is_returned_to_model() -> None:
    registry, adapter = fake_registry(
        [
            model_response(
                tool_calls=(
                    ToolCall(
                        id="missing-1",
                        name="missing_tool",
                        arguments={},
                    ),
                )
            ),
            model_response(content="工具不可用，已停止该操作"),
        ]
    )

    result = await AgentRuntime(
        registry,
        ToolRegistry(),
        provider="fake",
    ).run("调用不存在的工具")

    assert result.content == "工具不可用，已停止该操作"
    tool_result = json.loads(adapter.requests[1].messages[-1].content or "{}")
    assert tool_result["success"] is False
    assert "not found" in tool_result["error"].lower()


@pytest.mark.asyncio
async def test_three_identical_tool_calls_stop_before_third_execution() -> None:
    repeated_calls = [
        model_response(
            tool_calls=(
                ToolCall(
                    id=f"count-{index}",
                    name="count",
                    arguments={"value": 1},
                ),
            )
        )
        for index in range(3)
    ]
    registry, _ = fake_registry(repeated_calls)
    counting_tool = CountingTool()
    tools = ToolRegistry()
    tools.register(counting_tool)

    result = await AgentRuntime(registry, tools, provider="fake").run("count")

    assert counting_tool.executions == 2
    assert "3 consecutive times" in (result.content or "")


@pytest.mark.asyncio
async def test_max_steps_stops_the_loop() -> None:
    registry, adapter = fake_registry(
        [
            model_response(
                tool_calls=(
                    ToolCall(
                        id=f"count-{index}",
                        name="count",
                        arguments={"value": index},
                    ),
                )
            )
            for index in range(2)
        ]
    )
    tools = ToolRegistry()
    tools.register(CountingTool())

    result = await AgentRuntime(
        registry,
        tools,
        provider="fake",
        max_steps=2,
    ).run("keep counting")

    assert len(adapter.requests) == 2
    assert "maximum step limit (2) reached" in (result.content or "")
