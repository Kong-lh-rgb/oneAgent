"""把工具生命周期转换为 AgentEvent。"""

from __future__ import annotations

from typing import Any, Protocol

from app.models.types import ToolResult
from app.tools.approval import ApprovalDecision, ApprovalRequest
from app.tools.hooks import ToolExecutionContext, ToolHook

from .events import AgentEventType


class AgentEventEmitter(Protocol):
    """Agent 工具事件 Hook 所需的最小发射接口。"""

    async def emit(self, event_type: AgentEventType, **payload: Any) -> None:
        """发送一个 Agent 事件。"""


class AgentEventHook(ToolHook):
    """将工具执行阶段映射到现有 Agent 事件模型。"""

    def __init__(self, emitter: AgentEventEmitter) -> None:
        self._emitter = emitter

    async def before_execute(self, context: ToolExecutionContext) -> None:
        await self._emitter.emit(
            AgentEventType.TOOL_STARTED,
            step=context.step,
            tool_call=context.tool_call,
        )

    async def on_approval_required(
        self,
        context: ToolExecutionContext,
        request: ApprovalRequest,
    ) -> None:
        await self._emitter.emit(
            AgentEventType.TOOL_APPROVAL_REQUIRED,
            step=context.step,
            tool_call=context.tool_call,
        )

    async def on_approval_completed(
        self,
        context: ToolExecutionContext,
        request: ApprovalRequest,
        decision: ApprovalDecision,
    ) -> None:
        await self._emitter.emit(
            AgentEventType.TOOL_APPROVAL_COMPLETED,
            step=context.step,
            tool_call=context.tool_call,
            approval_decision=decision,
        )

    async def after_execute(
        self,
        context: ToolExecutionContext,
        result: ToolResult,
    ) -> None:
        await self._emitter.emit(
            AgentEventType.TOOL_COMPLETED,
            step=context.step,
            tool_call=context.tool_call,
            tool_result=result,
        )


__all__ = ["AgentEventEmitter", "AgentEventHook"]
