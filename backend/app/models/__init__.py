"""模型适配层的公共接口。"""

from .adapter import ModelAdapter
from .config import ModelSettings, ProviderConfig
from .errors import (
    ModelAdapterError,
    ProviderNotConfiguredError,
    UnsupportedMessageError,
    UnsupportedProviderError,
)
from .registry import ModelAdapterRegistry
from .types import (
    ApiStyle,
    Message,
    MessageRole,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    ToolCall,
    ToolDefinition,
    ToolPermission,
    ToolResult,
)

__all__ = [
    "ApiStyle",
    "Message",
    "MessageRole",
    "ModelAdapter",
    "ModelAdapterError",
    "ModelAdapterRegistry",
    "ModelProvider",
    "ModelRequest",
    "ModelResponse",
    "ModelSettings",
    "ModelUsage",
    "ProviderConfig",
    "ProviderNotConfiguredError",
    "ToolCall",
    "ToolDefinition",
    "ToolPermission",
    "ToolResult",
    "UnsupportedMessageError",
    "UnsupportedProviderError",
]
