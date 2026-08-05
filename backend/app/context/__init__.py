"""上下文管理：token 估算、模型能力、输入预算、消息块与发送前上下文准备。"""

from .blocks import (
    BlockType,
    ConversationBlock,
    MalformedToolBlock,
    MessageBlock,
    SystemBlock,
    ToolRoundBlock,
    partition_messages,
)
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
from .history import compact_model_blocks, compact_model_history
from .manager import ContextCompactionStage, ContextDecision, ContextManager
from .reducers import ToolReducer, ToolReductionResult
from .tokens import TokenEstimator, default_token_estimator, model_family

__all__ = [
    "BlockType",
    "CapabilitySource",
    "ContextBudget",
    "ContextBudgetPolicy",
    "ContextCompactionStage",
    "ContextDecision",
    "ContextManager",
    "ContextSettings",
    "ConversationBlock",
    "FALLBACK_CONTEXT_WINDOW",
    "MalformedToolBlock",
    "MessageBlock",
    "ModelCapabilities",
    "ModelCapabilityRegistry",
    "SystemBlock",
    "TokenEstimator",
    "ToolRoundBlock",
    "ToolReducer",
    "ToolReductionResult",
    "build_budget_policy",
    "build_model_capability_registry",
    "compact_model_blocks",
    "compact_model_history",
    "default_token_estimator",
    "model_family",
    "partition_messages",
]
