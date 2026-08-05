"""上下文管理器：决定每次模型调用实际发送的上下文。

当前阶段只做 token 估算并原样返回消息，**不压缩**；
后续在此加入窗口预算检查与旧消息裁剪（滚动摘要）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.models.types import Message, ToolDefinition

from .tokens import TokenEstimator, default_token_estimator


@dataclass(frozen=True)
class ContextDecision:
    """一次模型调用最终发送的上下文与统计信息。"""

    messages: tuple[Message, ...]
    tools: tuple[ToolDefinition, ...]
    estimated_input_tokens: int | None = None
    trimmed: bool = False
    reason: str | None = None


class ContextManager:
    """准备模型请求上下文；当前不压缩，原样返回。"""

    def __init__(self, estimator: TokenEstimator | None = None) -> None:
        self._estimator = estimator or default_token_estimator()

    @property
    def estimator(self) -> TokenEstimator:
        return self._estimator

    async def prepare(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolDefinition] = (),
        model: str | None = None,
        provider: str | None = None,
    ) -> ContextDecision:
        """返回要发送给模型的上下文与估算。

        当前阶段不裁剪，消息原样返回；估算用于可观测与后续预算决策。
        """

        estimated = self._estimator.estimate_request(
            messages,
            tools=tools,
            model=model,
            provider=provider,
        )
        return ContextDecision(
            messages=tuple(messages),
            tools=tuple(tools),
            estimated_input_tokens=estimated,
            trimmed=False,
        )


__all__ = ["ContextDecision", "ContextManager"]
