"""工具权限（ALLOWED / HUMAN_APPROVAL / FORBIDDEN）与可观测性测试。"""

from __future__ import annotations

from typing import Any

import pytest

from app.models.types import ToolCall, ToolDefinition, ToolPermission
from app.tools import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalRequest,
    ApprovalResponse,
    AutoApproveGate,
    BaseTool,
    ToolExecutor,
    ToolRegistry,
)


class StubTool(BaseTool):
    def __init__(self, name: str, permission: ToolPermission) -> None:
        self._name = name
        self._permission = permission
        self.executions = 0

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self._name,
            description=f"stub {self._name}",
            permission=self._permission,
        )

    async def execute(self, arguments: dict[str, Any]) -> str:
        self.executions += 1
        return f"ran:{self._name}"


class RecordingGate(ApprovalGate):
    def __init__(self, decision: ApprovalDecision) -> None:
        self.decision = decision
        self.requests: list[ApprovalRequest] = []

    async def request_approval(self, request: ApprovalRequest) -> ApprovalResponse:
        self.requests.append(request)
        return ApprovalResponse(decision=self.decision)


def build(*tools: StubTool) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return registry


@pytest.mark.asyncio
async def test_forbidden_tools_are_hidden_from_model_definitions() -> None:
    registry = build(
        StubTool("allowed_tool", ToolPermission.ALLOWED),
        StubTool("review_tool", ToolPermission.HUMAN_APPROVAL),
        StubTool("secret_tool", ToolPermission.FORBIDDEN),
    )

    names = {definition.name for definition in registry.definitions()}
    assert names == {"allowed_tool", "review_tool"}
    assert "secret_tool" not in names


@pytest.mark.asyncio
async def test_forbidden_tool_is_blocked_even_with_auto_approve() -> None:
    secret = StubTool("secret_tool", ToolPermission.FORBIDDEN)
    executor = ToolExecutor(build(secret), approval_gate=AutoApproveGate())

    result = await executor.execute(
        ToolCall(id="s-1", name="secret_tool", arguments={})
    )

    assert result.success is False
    assert "forbidden" in (result.error or "")
    assert secret.executions == 0


@pytest.mark.asyncio
async def test_human_approval_denied_by_default_gate() -> None:
    tool = StubTool("review_tool", ToolPermission.HUMAN_APPROVAL)

    result = await ToolExecutor(build(tool)).execute(
        ToolCall(id="r-1", name="review_tool", arguments={})
    )

    assert result.success is False
    assert "human approval" in (result.error or "")
    assert tool.executions == 0


@pytest.mark.asyncio
async def test_human_approval_denied_captures_gate_request() -> None:
    gate = RecordingGate(ApprovalDecision.DENIED)
    executor = ToolExecutor(
        build(StubTool("review_tool", ToolPermission.HUMAN_APPROVAL)),
        approval_gate=gate,
    )

    result = await executor.execute(
        ToolCall(id="r-1", name="review_tool", arguments={"a": 1})
    )

    assert result.success is False
    assert len(gate.requests) == 1
    assert gate.requests[0].tool_name == "review_tool"
    assert gate.requests[0].arguments == {"a": 1}
    assert "stub review_tool" in gate.requests[0].summary()
    assert '{"a":1}' in gate.requests[0].summary()


@pytest.mark.asyncio
async def test_human_approval_approved_executes() -> None:
    gate = RecordingGate(ApprovalDecision.APPROVED)
    tool = StubTool("review_tool", ToolPermission.HUMAN_APPROVAL)
    executor = ToolExecutor(build(tool), approval_gate=gate)

    result = await executor.execute(
        ToolCall(id="r-1", name="review_tool", arguments={})
    )

    assert result.success is True
    assert result.output == "ran:review_tool"
    assert tool.executions == 1


@pytest.mark.asyncio
async def test_auto_approve_gate_runs_human_approval_tools() -> None:
    tool = StubTool("review_tool", ToolPermission.HUMAN_APPROVAL)
    executor = ToolExecutor(build(tool), approval_gate=AutoApproveGate())

    result = await executor.execute(
        ToolCall(id="r-1", name="review_tool", arguments={})
    )

    assert result.success is True
    assert tool.executions == 1


@pytest.mark.asyncio
async def test_execution_records_capture_success_and_failures() -> None:
    executor = ToolExecutor(
        build(
            StubTool("ok_tool", ToolPermission.ALLOWED),
            StubTool("bad_tool", ToolPermission.FORBIDDEN),
        )
    )

    await executor.execute(ToolCall(id="1", name="ok_tool", arguments={}))
    await executor.execute(ToolCall(id="2", name="bad_tool", arguments={}))
    await executor.execute(ToolCall(id="3", name="missing_tool", arguments={}))

    records = executor.execution_records
    assert len(records) == 3

    ok = records[0]
    assert ok.tool_name == "ok_tool"
    assert ok.success is True
    assert ok.error is None
    assert ok.permission == ToolPermission.ALLOWED.value
    assert ok.duration_ms >= 0

    bad = records[1]
    assert bad.success is False
    assert bad.error is not None
    assert "forbidden" in bad.error

    missing = records[2]
    assert missing.success is False
    assert "not found" in (missing.error or "").lower()


@pytest.mark.asyncio
async def test_in_memory_logger_respects_maxlen() -> None:
    from app.tools import InMemoryExecutionLogger

    logger = InMemoryExecutionLogger(maxlen=2)
    executor = ToolExecutor(build(StubTool("t", ToolPermission.ALLOWED)), logger=logger)

    for index in range(3):
        await executor.execute(
            ToolCall(id=f"{index}", name="t", arguments={})
        )

    assert logger.count == 2
    assert [r.tool_call_id for r in logger.recent()] == ["1", "2"]


def test_registry_name_validation_and_unregister() -> None:
    registry = ToolRegistry()

    with pytest.raises(ValueError, match="empty"):
        registry.register(StubTool("", ToolPermission.ALLOWED))
    with pytest.raises(ValueError, match="letters, digits"):
        registry.register(StubTool("bad name", ToolPermission.ALLOWED))

    tool = StubTool("good_name", ToolPermission.ALLOWED)
    registry.register(tool)
    assert registry.names() == ("good_name",)
    assert registry.unregister("good_name") is tool
    with pytest.raises(KeyError):
        registry.get("good_name")
