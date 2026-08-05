"""工具生命周期 Hook、权限控制与故障隔离测试。"""

from __future__ import annotations

from typing import Any

import pytest

from app.models.types import ToolCall, ToolDefinition, ToolPermission, ToolResult
from app.tools import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalRequest,
    ApprovalResponse,
    BaseTool,
    ToolExecutionContext,
    ToolExecutor,
    ToolHook,
    ToolRegistry,
)


class HookTool(BaseTool):
    def __init__(self, permission: ToolPermission = ToolPermission.ALLOWED) -> None:
        self._permission = permission
        self.executions = 0

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="hook_tool",
            description="用于验证 Hook 生命周期",
            permission=self._permission,
        )

    async def execute(self, arguments: dict[str, Any]) -> str:
        self.executions += 1
        return f"value:{arguments['value']}"


class RecordingHook(ToolHook):
    def __init__(self) -> None:
        self.events: list[str] = []
        self.contexts: list[ToolExecutionContext] = []
        self.results: list[ToolResult] = []

    async def before_execute(self, context: ToolExecutionContext) -> None:
        self.events.append("before")
        self.contexts.append(context)

    async def on_approval_required(
        self,
        context: ToolExecutionContext,
        request: ApprovalRequest,
    ) -> None:
        self.events.append("approval_required")

    async def on_approval_completed(
        self,
        context: ToolExecutionContext,
        request: ApprovalRequest,
        decision: ApprovalDecision,
        rule: Any = None,
    ) -> None:
        suffix = f":{rule.id}" if rule is not None else ""
        self.events.append(f"approval_completed:{decision.value}{suffix}")

    async def after_execute(
        self,
        context: ToolExecutionContext,
        result: ToolResult,
    ) -> None:
        self.events.append("after")
        self.results.append(result)


class FailingHook(ToolHook):
    async def before_execute(self, context: ToolExecutionContext) -> None:
        raise RuntimeError("before unavailable")

    async def after_execute(
        self,
        context: ToolExecutionContext,
        result: ToolResult,
    ) -> None:
        raise RuntimeError("after unavailable")


class FailingCriticalHook(ToolHook):
    critical = True

    async def before_execute(self, context: ToolExecutionContext) -> None:
        raise RuntimeError("security policy unavailable")


class FixedGate(ApprovalGate):
    def __init__(self, decision: ApprovalDecision) -> None:
        self._decision = decision

    async def request_approval(self, request: ApprovalRequest) -> ApprovalResponse:
        return ApprovalResponse(decision=self._decision)


class FailingGate(ApprovalGate):
    async def request_approval(self, request: ApprovalRequest) -> ApprovalResponse:
        raise RuntimeError("approval service unavailable")


def build_registry(tool: BaseTool) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(tool)
    return registry


@pytest.mark.asyncio
async def test_hooks_receive_context_and_success_result() -> None:
    tool = HookTool()
    hook = RecordingHook()
    executor = ToolExecutor(build_registry(tool), hooks=(hook,))
    call = ToolCall(id="hook-1", name="hook_tool", arguments={"value": 7})

    result = await executor.execute(
        call,
        context=ToolExecutionContext(
            tool_call=call,
            run_id="run-1",
            conversation_id="conversation-1",
            step=2,
        ),
    )

    assert result.success is True
    assert hook.events == ["before", "after"]
    assert hook.results == [result]
    context = hook.contexts[0]
    assert context.run_id == "run-1"
    assert context.conversation_id == "conversation-1"
    assert context.step == 2
    assert context.tool_definition == tool.definition
    assert context.arguments == {"value": 7}
    assert "started_at" in context.metadata


@pytest.mark.asyncio
async def test_approval_lifecycle_is_dispatched_in_order() -> None:
    tool = HookTool(ToolPermission.HUMAN_APPROVAL)
    hook = RecordingHook()
    executor = ToolExecutor(
        build_registry(tool),
        approval_gate=FixedGate(ApprovalDecision.APPROVED),
        hooks=(hook,),
    )

    result = await executor.execute(
        ToolCall(id="hook-2", name="hook_tool", arguments={"value": 8})
    )

    assert result.success is True
    assert hook.events == [
        "before",
        "approval_required",
        "approval_completed:approved",
        "after",
    ]


@pytest.mark.asyncio
async def test_observer_hook_failure_does_not_change_tool_result() -> None:
    tool = HookTool()
    executor = ToolExecutor(build_registry(tool), hooks=(FailingHook(),))

    result = await executor.execute(
        ToolCall(id="hook-3", name="hook_tool", arguments={"value": 9})
    )

    assert result.success is True
    assert result.output == "value:9"
    assert tool.executions == 1
    assert len(executor.execution_records) == 1


@pytest.mark.asyncio
async def test_custom_hooks_cannot_bypass_forbidden_permission() -> None:
    tool = HookTool(ToolPermission.FORBIDDEN)
    hook = RecordingHook()
    executor = ToolExecutor(build_registry(tool), hooks=(hook,))

    result = await executor.execute(
        ToolCall(id="hook-4", name="hook_tool", arguments={"value": 10})
    )

    assert result.success is False
    assert "forbidden" in (result.error or "")
    assert tool.executions == 0
    assert hook.events == ["before", "after"]


@pytest.mark.asyncio
async def test_critical_hook_failure_denies_execution() -> None:
    tool = HookTool()
    executor = ToolExecutor(
        build_registry(tool),
        hooks=(FailingCriticalHook(),),
    )

    result = await executor.execute(
        ToolCall(id="hook-critical", name="hook_tool", arguments={"value": 10})
    )

    assert result.success is False
    assert "Critical tool hook failed" in (result.error or "")
    assert tool.executions == 0


@pytest.mark.asyncio
async def test_permission_gate_failure_is_closed_and_recorded() -> None:
    tool = HookTool(ToolPermission.HUMAN_APPROVAL)
    hook = RecordingHook()
    executor = ToolExecutor(
        build_registry(tool),
        approval_gate=FailingGate(),
        hooks=(hook,),
    )

    result = await executor.execute(
        ToolCall(id="hook-5", name="hook_tool", arguments={"value": 11})
    )

    assert result.success is False
    assert "Permission check failed" in (result.error or "")
    assert tool.executions == 0
    assert hook.events == ["before", "approval_required", "after"]
    assert executor.execution_records[0].error == result.error
