"""DesktopApprovalGate：面向 Desktop / Automation 的异步审批门。

与 ConsoleApprovalGate（终端 input）不同，它不依赖终端输入：

    Agent 执行危险 Tool
        ↓ PermissionHook 发现 HUMAN_APPROVAL
    DesktopApprovalGate.request_approval(request)
        ↓ 持久化 PENDING ApprovalRequest + 广播 approval.required
    等待 asyncio.Future（只阻塞该 Run 的 task，不阻塞事件循环）
        ↓ Desktop 通过 approval.approve / approval.deny
    返回 ApprovalResponse
        ↓ Agent 继续执行 / 拒绝

关键约束：
- Agent 等待审批时不阻塞整个 WebSocket：request_approval 只在 Run 的
  asyncio task 内 await Future，事件循环空闲，WebSocket 接收循环仍能处理
  approval.approve / deny；
- Desktop 断线不会自动 approve / deny：连接断开不触发任何 resolve，
  审批只能由 RPC method 显式 approve / deny 完成（fail-open 只在用户明确
  approve 时发生，其余一律保持 PENDING 等待）；
- approve / deny 只能执行一次：由 SQLiteApprovalStore.resolve 在事务内保证，
  已 resolved 的记录不能再修改；
- 所有审批结果仍走现有 Trace / AgentEvent（AgentEventHook 的
  TOOL_APPROVAL_REQUIRED / TOOL_APPROVAL_COMPLETED），本 Gate 不介入事件。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from app.tools.approval import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalResponse,
)
from app.tools.approval import (
    ApprovalRequest as ApprovalSubmission,
)

from .models import ApprovalRequest as ApprovalRecord
from .models import ApprovalRequestStatus
from .store import SQLiteApprovalStore

# 审批结果通知广播器：method + params → 发送给所有已连接的 WebSocket。
Broadcaster = Callable[[str, Any], Awaitable[None]]


class DesktopApprovalGate(ApprovalGate):
    """把 HUMAN_APPROVAL 工具的审批请求持久化，并异步等待用户决定。"""

    def __init__(
        self,
        store: SQLiteApprovalStore,
        *,
        broadcaster: Broadcaster | None = None,
    ) -> None:
        self._store = store
        self._broadcaster = broadcaster
        # approval_id → Future，表示“正在等待用户决定的审批”。
        self._pending: dict[str, asyncio.Future[ApprovalResponse]] = {}

    def set_broadcaster(self, broadcaster: Broadcaster) -> None:
        """注入通知广播器（Server 在 application.start() 后注入 hub.broadcast）。"""

        self._broadcaster = broadcaster

    @property
    def pending_count(self) -> int:
        """当前进程内正在等待用户决定的审批数量。"""

        return len(self._pending)

    async def request_approval(
        self,
        request: ApprovalSubmission,
    ) -> ApprovalResponse:
        """创建持久化 PENDING 审批并等待用户 approve / deny。

        顺序保证：先创建记录 → 再创建并注册 Future → 最后广播 approval.required。
        这样 Desktop 在收到 approval.required 后立刻 approve / deny 时，
        Future 必然已注册，`_settle` 一定能唤醒等待中的 Run —— 不会出现
        "数据库已 resolved 但 Run 没被唤醒" 的竞态。
        """

        record = await self._store.create(
            run_id=request.run_id,
            conversation_id=request.conversation_id,
            tool_name=request.tool_name,
            tool_call_id=request.tool_call_id,
            arguments=request.arguments,
            reason=request.description,
            ui_scope=request.ui_scope,
        )
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ApprovalResponse] = loop.create_future()
        self._pending[record.id] = future
        await self._notify("approval.required", {"approval": record})
        try:
            return await future
        finally:
            self._pending.pop(record.id, None)

    async def approve(self, approval_id: str) -> ApprovalRecord:
        """批准一个 PENDING 审批；已 resolved 时抛 ValueError。"""

        record = await self._store.resolve(
            approval_id,
            ApprovalRequestStatus.APPROVED,
        )
        self._settle(
            approval_id,
            ApprovalResponse(decision=ApprovalDecision.APPROVED),
        )
        await self._notify("approval.resolved", {"approval": record})
        return record

    async def deny(self, approval_id: str) -> ApprovalRecord:
        """拒绝一个 PENDING 审批；已 resolved 时抛 ValueError。"""

        record = await self._store.resolve(
            approval_id,
            ApprovalRequestStatus.DENIED,
        )
        self._settle(
            approval_id,
            ApprovalResponse(decision=ApprovalDecision.DENIED),
        )
        await self._notify("approval.resolved", {"approval": record})
        return record

    def _settle(self, approval_id: str, response: ApprovalResponse) -> None:
        """把审批结果交给正在等待的 Run task（若还在等待）。"""

        future = self._pending.get(approval_id)
        if future is not None and not future.done():
            future.set_result(response)

    async def _notify(self, method: str, params: Any) -> None:
        if self._broadcaster is not None:
            await self._broadcaster(method, params)


__all__ = ["DesktopApprovalGate"]
