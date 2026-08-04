"""不可绕过的工具权限控制 Hook。"""

from __future__ import annotations

from app.models.types import ToolPermission

from .approval import ApprovalDecision, ApprovalGate, ApprovalRequest
from .hooks import ToolExecutionContext, ToolHook, ToolHookDecision


class PermissionHook(ToolHook):
    """根据工具权限档位决定是否允许继续执行。"""

    critical = True

    def __init__(self, approval_gate: ApprovalGate) -> None:
        self._approval_gate = approval_gate

    async def before_execute(
        self,
        context: ToolExecutionContext,
    ) -> ToolHookDecision | None:
        definition = context.tool_definition
        if definition is None:
            return None

        permission = definition.permission
        if permission is ToolPermission.FORBIDDEN:
            return ToolHookDecision(
                denied_reason=(
                    f"Tool '{context.tool_call.name}' is forbidden "
                    "for model execution."
                )
            )
        if permission is ToolPermission.HUMAN_APPROVAL:
            return ToolHookDecision(
                approval_request=ApprovalRequest(
                    tool_call_id=context.tool_call.id,
                    tool_name=context.tool_call.name,
                    arguments=context.arguments or {},
                    description=definition.description,
                )
            )
        return None

    async def request_approval(
        self,
        request: ApprovalRequest,
    ) -> ApprovalDecision:
        """把审批请求交给配置的审批门。"""

        return await self._approval_gate.request_approval(request)

    @staticmethod
    def denied_reason(
        context: ToolExecutionContext,
        decision: ApprovalDecision,
    ) -> str | None:
        """审批未通过时返回拒绝原因。"""

        if decision is ApprovalDecision.APPROVED:
            return None
        return (
            f"Tool '{context.tool_call.name}' execution was denied "
            "(requires human approval)."
        )


__all__ = ["PermissionHook"]
