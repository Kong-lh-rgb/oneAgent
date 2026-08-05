"""上下文预算策略。

根据模型能力与本次 ``max_output_tokens`` 计算输入预算：

    input_budget  = context_window - reserved_output_tokens - safety_margin_tokens
    trigger_tokens = input_budget × trigger_ratio（默认 0.80）
    target_tokens  = input_budget × target_ratio（默认 0.60）

本次显式 ``max_output_tokens`` 优先于模型默认值；非法配置抛出清晰错误，
不允许静默产生负数预算。
"""

from __future__ import annotations

from dataclasses import dataclass

from .capabilities import ModelCapabilities
from .config import ContextSettings

DEFAULT_TRIGGER_RATIO = 0.80
DEFAULT_TARGET_RATIO = 0.60
DEFAULT_SAFETY_MARGIN_TOKENS = 4_096


@dataclass(frozen=True)
class ContextBudget:
    """一次模型调用的输入预算与压缩触发线。"""

    context_window: int
    reserved_output_tokens: int
    safety_margin_tokens: int
    input_budget: int
    trigger_tokens: int
    target_tokens: int


class ContextBudgetPolicy:
    """根据模型能力计算输入预算与压缩触发/目标线。"""

    def __init__(
        self,
        *,
        trigger_ratio: float = DEFAULT_TRIGGER_RATIO,
        target_ratio: float = DEFAULT_TARGET_RATIO,
        safety_margin_tokens: int = DEFAULT_SAFETY_MARGIN_TOKENS,
    ) -> None:
        if not 0.0 < trigger_ratio < 1.0:
            raise ValueError("trigger_ratio must be in (0, 1)")
        if not 0.0 < target_ratio < 1.0:
            raise ValueError("target_ratio must be in (0, 1)")
        if target_ratio >= trigger_ratio:
            raise ValueError("target_ratio must be lower than trigger_ratio")
        if safety_margin_tokens < 0:
            raise ValueError("safety_margin_tokens cannot be negative")
        self._trigger_ratio = trigger_ratio
        self._target_ratio = target_ratio
        self._safety_margin_tokens = safety_margin_tokens

    def compute(
        self,
        capabilities: ModelCapabilities,
        *,
        max_output_tokens: int | None = None,
    ) -> ContextBudget:
        """计算预算；本次显式 max_output_tokens 优先于模型默认值。"""

        context_window = capabilities.context_window
        reserved_output = (
            max_output_tokens
            if max_output_tokens is not None
            else capabilities.max_output_tokens
        )
        if reserved_output < 0:
            raise ValueError("max_output_tokens cannot be negative")

        input_budget = (
            context_window - reserved_output - self._safety_margin_tokens
        )
        if input_budget <= 0:
            raise ValueError(
                f"invalid context budget: window={context_window} "
                f"reserved_output={reserved_output} "
                f"safety_margin={self._safety_margin_tokens} "
                f"input_budget={input_budget} (must be > 0)"
            )
        return ContextBudget(
            context_window=context_window,
            reserved_output_tokens=reserved_output,
            safety_margin_tokens=self._safety_margin_tokens,
            input_budget=input_budget,
            trigger_tokens=int(input_budget * self._trigger_ratio),
            target_tokens=int(input_budget * self._target_ratio),
        )


def build_budget_policy(
    settings: ContextSettings | None = None,
) -> ContextBudgetPolicy:
    """从配置构建预算策略。"""

    resolved = settings or ContextSettings()
    return ContextBudgetPolicy(
        trigger_ratio=resolved.context_trigger_ratio,
        target_ratio=resolved.context_target_ratio,
        safety_margin_tokens=resolved.context_safety_margin_tokens,
    )


__all__ = [
    "ContextBudget",
    "ContextBudgetPolicy",
    "DEFAULT_SAFETY_MARGIN_TOKENS",
    "DEFAULT_TARGET_RATIO",
    "DEFAULT_TRIGGER_RATIO",
    "build_budget_policy",
]
