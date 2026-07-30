"""Public model adapter API."""

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
    "UnsupportedMessageError",
    "UnsupportedProviderError",
]
