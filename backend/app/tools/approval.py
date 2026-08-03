"""工具执行的人工审核（approval）门。

HUMAN_APPROVAL 档位的工具在执行前会调用 ``ApprovalGate``，
由人工决定批准或拒绝。默认使用 ``DenyAllGate`` 作为安全默认值。
"""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    DENIED = "denied"


@dataclass(frozen=True)
class ApprovalRequest:
    """提交给人工审核的请求上下文。"""

    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any]
    description: str = ""

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
    async def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        """返回批准或拒绝。"""


class AutoApproveGate(ApprovalGate):
    """自动批准所有审核请求（仅用于测试或完全信任的环境）。"""

    async def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision.APPROVED


class DenyAllGate(ApprovalGate):
    """拒绝所有审核请求（安全默认值）。"""

    async def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision.DENIED


class ConsoleApprovalGate(ApprovalGate):
    """在终端提示人工确认（y/N）。"""

    def __init__(self, *, prompt_prefix: str = "[人工审核]") -> None:
        self._prompt_prefix = prompt_prefix

    async def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        prompt = (
            f"\n{self._prompt_prefix}\n"
            f"{request.summary()}\n"
            f"允许执行吗? [y/N] "
        )
        answer = await asyncio.to_thread(input, prompt)
        return (
            ApprovalDecision.APPROVED
            if answer.strip().lower() in {"y", "yes"}
            else ApprovalDecision.DENIED
        )


__all__ = [
    "ApprovalDecision",
    "ApprovalGate",
    "ApprovalRequest",
    "AutoApproveGate",
    "ConsoleApprovalGate",
    "DenyAllGate",
]
