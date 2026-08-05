"""上下文管理：token 估算、模型能力、输入预算与发送前上下文准备。"""

from .budget import (
    ContextBudget,
    ContextBudgetPolicy,
    build_budget_policy,
)
from .capabilities import (
    FALLBACK_CONTEXT_WINDOW,
    CapabilitySource,
    ModelCapabilities,
    ModelCapabilityRegistry,
    build_model_capability_registry,
)
from .config import ContextSettings
from .manager import ContextDecision, ContextManager
from .tokens import TokenEstimator, default_token_estimator, model_family

__all__ = [
    "CapabilitySource",
    "ContextBudget",
    "ContextBudgetPolicy",
    "ContextDecision",
    "ContextManager",
    "ContextSettings",
    "FALLBACK_CONTEXT_WINDOW",
    "ModelCapabilities",
    "ModelCapabilityRegistry",
    "TokenEstimator",
    "build_budget_policy",
    "build_model_capability_registry",
    "default_token_estimator",
    "model_family",
]
