"""工具执行的人工审核（approval）门。

HUMAN_APPROVAL 档位的工具在执行前会调用 ``ApprovalGate``，
由人工决定批准或拒绝。默认使用 ``DenyAllGate`` 作为安全默认值。

``ApprovalScope`` 与 ``ApprovalResponse`` 也定义在这里（审批基元），
``permissions.models`` 会从本模块导入并重新导出，避免循环导入。
"""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    DENIED = "denied"


class ApprovalScope(StrEnum):
    """审批通过后规则的作用范围。"""

    ONCE = "once"  # 仅允许这一次，不创建规则
    RUN = "run"  # 当前 Run 内允许完全相同的操作
    CONVERSATION = "conversation"  # 当前会话内记住完全相同的操作


class ApprovalResponse(BaseModel):
    """ApprovalGate 返回的用户选择。"""

    model_config = ConfigDict(extra="forbid")

    decision: ApprovalDecision
    scope: ApprovalScope = ApprovalScope.ONCE


@dataclass(frozen=True)
class ApprovalRequest:
    """提交给人工审核的请求上下文。

    ``run_id / conversation_id`` 是可选溯源：桌面等异步审批门需要它们把
    ApprovalRequest 关联到对应 Run / Conversation（持久化为 ApprovalRequest 记录）。
    """

    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any]
    description: str = ""
    run_id: str | None = None
    conversation_id: str | None = None
    # 声明式审批落点：sandbox（进 Chat）/ desktop（跟随用户注意力，可进浮窗）。
    ui_scope: str = "sandbox"

    def summary(self, *, max_arguments: int = 500) -> str:
        serialized = json.dumps(
            self.arguments,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if len(serialized) > max_arguments:
            serialized = serialized[:max_arguments] + "…"
        lines = [
            f"工具: {self.tool_name}",
            f"说明: {self.description or '(无)'}",
            f"参数: {serialized}",
        ]
        return "\n".join(lines)


class ApprovalGate(ABC):
    """决定 HUMAN_APPROVAL 工具是否可以执行。"""

    @abstractmethod
    async def request_approval(self, request: ApprovalRequest) -> ApprovalResponse:
        """返回用户的选择（决定 + 可选的作用范围）。"""


class AutoApproveGate(ApprovalGate):
    """自动批准所有审核请求（仅用于测试或完全信任的环境）。"""

    async def request_approval(self, request: ApprovalRequest) -> ApprovalResponse:
        return ApprovalResponse(decision=ApprovalDecision.APPROVED)


class DenyAllGate(ApprovalGate):
    """拒绝所有审核请求（安全默认值）。"""

    async def request_approval(self, request: ApprovalRequest) -> ApprovalResponse:
        return ApprovalResponse(decision=ApprovalDecision.DENIED)


class ConsoleApprovalGate(ApprovalGate):
    """在终端提示人工选择：仅此一次 / 本 Run 相同操作 / 记住安全规则 / 拒绝。

    ``rule_label_factory`` 用于生成第 3 项的描述（如“当前工作允许 pytest 命令”），
    该逻辑属于权限规则子系统，不属于审批门本身。
    """

    def __init__(
        self,
        *,
        prompt_prefix: str = "[人工审核]",
        rule_label_factory: Callable[[ApprovalRequest], str] | None = None,
    ) -> None:
        self._prompt_prefix = prompt_prefix
        self._rule_label_factory = rule_label_factory

    async def request_approval(self, request: ApprovalRequest) -> ApprovalResponse:
        label = (
            self._rule_label_factory(request)
            if self._rule_label_factory is not None
            else "当前工作记住该安全规则（相同参数自动通过）"
        )
        prompt = (
            f"\n{self._prompt_prefix}\n"
            f"{request.summary()}\n"
            f"1. 仅允许这一次\n"
            f"2. 当前 Run 内允许完全相同的操作\n"
            f"3. {label}\n"
            f"4. 拒绝\n"
            f"请选择 [1/2/3/4]: "
        )
        answer = (await asyncio.to_thread(input, prompt)).strip()
        if answer == "2":
            return ApprovalResponse(
                decision=ApprovalDecision.APPROVED,
                scope=ApprovalScope.RUN,
            )
        if answer == "3":
            return ApprovalResponse(
                decision=ApprovalDecision.APPROVED,
                scope=ApprovalScope.CONVERSATION,
            )
        if answer == "1":
            return ApprovalResponse(decision=ApprovalDecision.APPROVED)
        # 未识别输入按拒绝处理（fail-closed）
        return ApprovalResponse(decision=ApprovalDecision.DENIED)


__all__ = [
    "ApprovalDecision",
    "ApprovalGate",
    "ApprovalRequest",
    "ApprovalResponse",
    "ApprovalScope",
    "AutoApproveGate",
    "ConsoleApprovalGate",
    "DenyAllGate",
]
