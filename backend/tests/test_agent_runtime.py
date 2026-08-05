from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence

import pytest
from pydantic import SecretStr

from app.agent.events import (
    AgentEvent,
    AgentEventHandler,
    AgentEventType,
    InMemoryEventHandler,
)
from app.agent.result import AgentStopReason
from app.agent.runtime import AgentRuntime
from app.context import (
    ContextBudgetPolicy,
    ContextManager,
    ContextSettings,
    ModelCapabilityRegistry,
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
    ModelUsage,
    ToolCall,
    ToolDefinition,
    ToolPermission,
)
from app.tools.approval import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalRequest,
    ApprovalResponse,
    ApprovalScope,
    AutoApproveGate,
    DenyAllGate,
)
from app.tools.base import BaseTool
from app.tools.builtin.read_file import ReadFileTool
from app.tools.builtin.write_file import WriteFileTool
from app.tools.permissions.store import InMemoryPermissionRuleStore
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


class ApprovalCountingTool(CountingTool):
    definition = ToolDefinition(
        name="approval_count",
        description="Count after approval",
        parameters={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
        },
        permission=ToolPermission.HUMAN_APPROVAL,
    )


class RememberRunGate(ApprovalGate):
    """批准并只在当前 Run 内记住操作。"""

    async def request_approval(self, request: ApprovalRequest) -> ApprovalResponse:
        return ApprovalResponse(
            decision=ApprovalDecision.APPROVED,
            scope=ApprovalScope.RUN,
        )


class FailingEventHandler(AgentEventHandler):
    async def emit(self, event: AgentEvent) -> None:
        raise RuntimeError("event sink unavailable")


class BlockingModelAdapter(ModelAdapter):
    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self.started = asyncio.Event()
        self.cancelled = False

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        raise AssertionError("阻塞模型不应正常完成")

    async def close(self) -> None:
        pass


def model_response(
    *,
    content: str | None = None,
    tool_calls: tuple[ToolCall, ...] = (),
    usage: ModelUsage | None = None,
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
        usage=usage or ModelUsage(),
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
                ),
                usage=ModelUsage(
                    input_tokens=10,
                    output_tokens=2,
                    total_tokens=12,
                ),
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
                ),
                usage=ModelUsage(
                    input_tokens=20,
                    output_tokens=3,
                    total_tokens=23,
                ),
            ),
            model_response(
                content="摘要已写入 output.md",
                usage=ModelUsage(
                    input_tokens=30,
                    output_tokens=4,
                    total_tokens=34,
                ),
            ),
        ]
    )
    tools = ToolRegistry()
    tools.register(ReadFileTool(tmp_path))
    tools.register(WriteFileTool(tmp_path))
    event_handler = InMemoryEventHandler()
    initial_history = (
        Message(role=MessageRole.SYSTEM, content="你是本地文件助理。"),
    )

    result = await AgentRuntime(
        registry,
        tools,
        provider="fake",
        max_output_tokens=256,
    ).run(
        "读取 input.txt，生成摘要并写入 output.md",
        history=initial_history,
        conversation_id="conversation-1",
        event_handler=event_handler,
    )

    assert result.content == "摘要已写入 output.md"
    assert len(result.run_id) == 32
    assert result.ok is True
    assert result.steps == 3
    assert result.stop_reason is AgentStopReason.FINAL_ANSWER
    assert result.error is None
    assert result.messages[0] == initial_history[0]
    assert result.messages[-1] == result.final_message
    assert [message.role for message in result.messages] == [
        MessageRole.SYSTEM,
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
    ]
    assert result.usage == ModelUsage(
        input_tokens=60,
        output_tokens=9,
        total_tokens=69,
    )
    assert len(result.tool_rounds) == 2
    assert len(result.tool_calls) == 2
    assert [record.tool_call.name for record in result.tool_calls] == [
        "read_file",
        "write_file",
    ]
    assert result.tool_rounds[0].round_index == 0
    assert result.tool_rounds[0].records == (result.tool_calls[0],)
    assert result.tool_rounds[1].round_index == 1
    assert result.tool_rounds[1].records == (result.tool_calls[1],)
    events = event_handler.events
    assert [event.type for event in events] == [
        AgentEventType.AGENT_STARTED,
        AgentEventType.MODEL_STARTED,
        AgentEventType.MODEL_COMPLETED,
        AgentEventType.TOOL_STARTED,
        AgentEventType.TOOL_COMPLETED,
        AgentEventType.MODEL_STARTED,
        AgentEventType.MODEL_COMPLETED,
        AgentEventType.TOOL_STARTED,
        AgentEventType.TOOL_COMPLETED,
        AgentEventType.MODEL_STARTED,
        AgentEventType.MODEL_COMPLETED,
        AgentEventType.AGENT_COMPLETED,
    ]
    assert [event.sequence for event in events] == list(range(len(events)))
    assert {event.run_id for event in events} == {result.run_id}
    assert {event.conversation_id for event in events} == {"conversation-1"}
    assert events[-1].message == result.final_message
    assert events[-1].usage == result.usage
    completed_tools = [
        event for event in events if event.type is AgentEventType.TOOL_COMPLETED
    ]
    assert [event.tool_result for event in completed_tools] == [
        result.tool_calls[0].result,
        result.tool_calls[1].result,
    ]
    assert (tmp_path / "output.md").read_text(encoding="utf-8") == (
        "# 摘要\nOneAgent 能调用本地文件工具。"
    )
    assert len(adapter.requests) == 3
    assert all(request.max_output_tokens == 256 for request in adapter.requests)
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
async def test_runtime_below_trigger_sends_complete_history_to_model() -> None:
    older_call = ToolCall(
        id="older-count",
        name="count",
        arguments={"value": 1},
    )
    recent_call = ToolCall(
        id="recent-count",
        name="count",
        arguments={"value": 2},
    )
    history = (
        Message(role=MessageRole.USER, content="较旧一轮"),
        Message(role=MessageRole.ASSISTANT, tool_calls=(older_call,)),
        Message(
            role=MessageRole.TOOL,
            tool_call_id=older_call.id,
            name=older_call.name,
            content="1",
        ),
        Message(role=MessageRole.ASSISTANT, content="较旧一轮完成"),
        Message(role=MessageRole.USER, content="最近一轮"),
        Message(role=MessageRole.ASSISTANT, tool_calls=(recent_call,)),
        Message(
            role=MessageRole.TOOL,
            tool_call_id=recent_call.id,
            name=recent_call.name,
            content="2",
        ),
        Message(role=MessageRole.ASSISTANT, content="最近一轮完成"),
    )
    current_call = ToolCall(
        id="current-count",
        name="count",
        arguments={"value": 2},
    )
    registry, adapter = fake_registry(
        [
            model_response(tool_calls=(current_call,)),
            model_response(content="这一轮完成"),
        ]
    )
    tools = ToolRegistry()
    tools.register(CountingTool())

    result = await AgentRuntime(registry, tools, provider="fake").run(
        "这一轮",
        history=history,
    )

    assert result.messages[: len(history)] == history
    assert result.messages[1].tool_calls == (older_call,)
    assert result.messages[2].role is MessageRole.TOOL
    first_request = adapter.requests[0].messages
    assert first_request == (
        *history,
        Message(role=MessageRole.USER, content="这一轮"),
    )
    second_request = adapter.requests[1].messages
    assert any(older_call in message.tool_calls for message in second_request)
    assert any(
        recent_call in message.tool_calls
        for message in second_request
    )
    assert second_request[-2].tool_calls == (current_call,)
    assert second_request[-1].role is MessageRole.TOOL
    assert second_request[-1].tool_call_id == current_call.id


@pytest.mark.asyncio
async def test_runtime_sends_compressed_copy_but_returns_complete_raw_history() -> None:
    older_call = ToolCall(
        id="older-search",
        name="count",
        arguments={"value": 1},
    )
    recent_calls = (
        ToolCall(id="recent-1", name="count", arguments={"value": 2}),
        ToolCall(id="recent-2", name="count", arguments={"value": 3}),
    )
    long_result = "x" * 4_000
    history = (
        Message(role=MessageRole.USER, content="旧问题"),
        Message(role=MessageRole.ASSISTANT, tool_calls=(older_call,)),
        Message(
            role=MessageRole.TOOL,
            name=older_call.name,
            tool_call_id=older_call.id,
            content=long_result,
        ),
        Message(role=MessageRole.ASSISTANT, content="旧回答"),
        Message(role=MessageRole.ASSISTANT, tool_calls=(recent_calls[0],)),
        Message(
            role=MessageRole.TOOL,
            name=recent_calls[0].name,
            tool_call_id=recent_calls[0].id,
            content="最近结果一",
        ),
        Message(role=MessageRole.ASSISTANT, tool_calls=(recent_calls[1],)),
        Message(
            role=MessageRole.TOOL,
            name=recent_calls[1].name,
            tool_call_id=recent_calls[1].id,
            content="最近结果二",
        ),
    )
    registry, adapter = fake_registry([model_response(content="压缩后回答")])
    capability_registry = ModelCapabilityRegistry()
    capability_registry.register_override(
        "fake",
        "fake-model",
        context_window=600,
        max_output_tokens=100,
    )
    context_manager = ContextManager(
        registry=capability_registry,
        budget_policy=ContextBudgetPolicy(safety_margin_tokens=0),
        context_settings=ContextSettings(
            _env_file=None,
            context_keep_recent_tool_rounds=2,
            context_max_tool_result_chars=100,
            context_tool_result_head_chars=20,
            context_tool_result_tail_chars=20,
        ),
    )

    result = await AgentRuntime(
        registry,
        ToolRegistry(),
        provider="fake",
        max_output_tokens=100,
        context_manager=context_manager,
    ).run("当前问题", history=history)

    assert result.ok is True
    assert result.messages[: len(history)] == history
    assert result.messages[2].content == long_result
    request_messages = adapter.requests[0].messages
    prepared_old_result = next(
        message
        for message in request_messages
        if message.tool_call_id == older_call.id
    )
    assert prepared_old_result.content != long_result
    assert "tool result compacted" in (prepared_old_result.content or "")
    assert all(
        any(
            call.id == recent_call.id
            for message in request_messages
            for call in message.tool_calls
        )
        for recent_call in recent_calls
    )


@pytest.mark.asyncio
async def test_model_error_returns_assistant_message() -> None:
    registry, _ = fake_registry([RuntimeError("model unavailable")])
    event_handler = InMemoryEventHandler()

    result = await AgentRuntime(
        registry,
        ToolRegistry(),
        provider="fake",
    ).run("hello", event_handler=event_handler)

    assert result.role == MessageRole.ASSISTANT
    assert result.ok is False
    assert result.steps == 1
    assert result.stop_reason is AgentStopReason.MODEL_ERROR
    assert result.error is not None
    assert result.error.type == "ModelInvocationError"
    assert result.messages[-1] == result.final_message
    assert result.usage == ModelUsage()
    assert [event.type for event in event_handler.events] == [
        AgentEventType.AGENT_STARTED,
        AgentEventType.MODEL_STARTED,
        AgentEventType.AGENT_FAILED,
    ]
    assert event_handler.events[-1].error == result.error
    assert event_handler.events[-1].stop_reason == result.stop_reason
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
    assert result.stop_reason is AgentStopReason.FINAL_ANSWER
    assert result.error is None
    assert result.messages[-1] == result.final_message
    assert len(result.tool_rounds) == 1
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].result.success is False
    assert result.tool_calls[0].result.tool_name == "missing_tool"
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
    assert result.ok is False
    assert result.steps == 3
    assert result.stop_reason is AgentStopReason.REPEATED_TOOL_CALL
    assert result.error is not None
    assert result.error.type == "RepeatedToolCallError"
    assert result.messages[-1] == result.final_message
    assert len(result.tool_rounds) == 2
    assert len(result.tool_calls) == 2
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
    assert result.ok is False
    assert result.steps == 2
    assert result.stop_reason is AgentStopReason.MAX_STEPS
    assert result.error is not None
    assert result.error.type == "MaxStepsExceededError"
    assert result.messages[-1] == result.final_message
    assert len(result.tool_rounds) == 2
    assert len(result.tool_calls) == 2
    assert "maximum step limit (2) reached" in (result.content or "")


@pytest.mark.asyncio
async def test_tool_round_budget_forces_final_answer_without_more_tools() -> None:
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
            for index in range(3)
        ]
        + [model_response(content="根据已有结果完成回答")]
    )
    tools = ToolRegistry()
    tools.register(CountingTool())

    result = await AgentRuntime(
        registry,
        tools,
        provider="fake",
        max_steps=10,
        max_tool_rounds=3,
    ).run("连续收集信息")

    assert result.ok is True
    assert result.steps == 4
    assert result.content == "根据已有结果完成回答"
    assert len(result.tool_rounds) == 3
    final_request = adapter.requests[-1]
    assert final_request.tools == ()
    assert final_request.tool_choice is None
    assert final_request.messages[-1].role is MessageRole.SYSTEM
    assert "停止调用工具" in (final_request.messages[-1].content or "")
    assert result.messages[-1] == result.final_message
    assert all(
        "工具调用轮次已用完" not in (message.content or "")
        for message in result.messages
    )


@pytest.mark.asyncio
async def test_event_handler_failure_does_not_stop_runtime() -> None:
    registry, _ = fake_registry([model_response(content="正常完成")])

    result = await AgentRuntime(
        registry,
        ToolRegistry(),
        provider="fake",
    ).run("hello", event_handler=FailingEventHandler())

    assert result.ok is True
    assert result.content == "正常完成"


@pytest.mark.asyncio
async def test_run_stream_returns_events_and_final_result() -> None:
    registry, _ = fake_registry([model_response(content="流式完成")])
    runtime = AgentRuntime(registry, ToolRegistry(), provider="fake")
    observer = InMemoryEventHandler()

    events = [
        event
        async for event in runtime.run_stream(
            "hello",
            conversation_id="conversation-1",
            event_handler=observer,
        )
    ]

    assert [event.type for event in events] == [
        AgentEventType.AGENT_STARTED,
        AgentEventType.MODEL_STARTED,
        AgentEventType.MODEL_COMPLETED,
        AgentEventType.AGENT_COMPLETED,
    ]
    final_result = events[-1].result
    assert final_result is not None
    assert final_result.content == "流式完成"
    assert final_result.run_id == events[-1].run_id
    assert {event.conversation_id for event in events} == {"conversation-1"}
    assert observer.events == tuple(events)


@pytest.mark.asyncio
async def test_runtime_emits_approval_required_and_completed_events() -> None:
    registry, _ = fake_registry(
        [
            model_response(
                tool_calls=(
                    ToolCall(
                        id="approval-1",
                        name="approval_count",
                        arguments={"value": 1},
                    ),
                )
            ),
            model_response(content="审批工具执行完成"),
        ]
    )
    tool = ApprovalCountingTool()
    tools = ToolRegistry()
    tools.register(tool)
    handler = InMemoryEventHandler()

    result = await AgentRuntime(
        registry,
        tools,
        provider="fake",
        approval_gate=AutoApproveGate(),
    ).run("执行审批工具", event_handler=handler)

    approval_events = [
        event
        for event in handler.events
        if event.type
        in {
            AgentEventType.TOOL_APPROVAL_REQUIRED,
            AgentEventType.TOOL_APPROVAL_COMPLETED,
        }
    ]
    assert [event.type for event in approval_events] == [
        AgentEventType.TOOL_APPROVAL_REQUIRED,
        AgentEventType.TOOL_APPROVAL_COMPLETED,
    ]
    assert approval_events[0].approval_decision is None
    assert approval_events[1].approval_decision is ApprovalDecision.APPROVED
    assert approval_events[0].tool_call == result.tool_calls[0].tool_call
    assert tool.executions == 1


@pytest.mark.asyncio
async def test_runtime_records_denied_approval_without_executing_tool() -> None:
    registry, _ = fake_registry(
        [
            model_response(
                tool_calls=(
                    ToolCall(
                        id="approval-1",
                        name="approval_count",
                        arguments={"value": 1},
                    ),
                )
            ),
            model_response(content="审批被拒绝"),
        ]
    )
    tool = ApprovalCountingTool()
    tools = ToolRegistry()
    tools.register(tool)
    handler = InMemoryEventHandler()

    result = await AgentRuntime(
        registry,
        tools,
        provider="fake",
        approval_gate=DenyAllGate(),
    ).run("执行审批工具", event_handler=handler)

    completed = next(
        event
        for event in handler.events
        if event.type is AgentEventType.TOOL_APPROVAL_COMPLETED
    )
    assert completed.approval_decision is ApprovalDecision.DENIED
    assert result.tool_calls[0].result.success is False
    assert tool.executions == 0


@pytest.mark.asyncio
async def test_runtime_cleans_run_scoped_permission_rules() -> None:
    registry, _ = fake_registry(
        [
            model_response(
                tool_calls=(
                    ToolCall(
                        id="approval-1",
                        name="approval_count",
                        arguments={"value": 1},
                    ),
                )
            ),
            model_response(content="完成"),
        ]
    )
    tools = ToolRegistry()
    tools.register(ApprovalCountingTool())
    store = InMemoryPermissionRuleStore()

    await AgentRuntime(
        registry,
        tools,
        provider="fake",
        approval_gate=RememberRunGate(),
        rule_store=store,
    ).run("执行审批工具", conversation_id="conversation-1")

    assert await store.list() == ()


@pytest.mark.asyncio
async def test_closing_run_stream_cancels_background_model_request() -> None:
    config = ProviderConfig(
        provider="blocking",
        model="blocking-model",
        api_key=SecretStr("offline-test-key"),
        api_style=ApiStyle.CHAT_COMPLETIONS,
    )
    adapter = BlockingModelAdapter(config)
    registry = ModelAdapterRegistry(ModelSettings(_env_file=None))
    registry.register("blocking", lambda _: adapter, config=config)
    runtime = AgentRuntime(registry, ToolRegistry(), provider="blocking")
    stream = runtime.run_stream("hello")

    first_event = await anext(stream)
    await adapter.started.wait()
    await stream.aclose()

    assert first_event.type is AgentEventType.AGENT_STARTED
    assert adapter.cancelled is True
