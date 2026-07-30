"""Built-in model provider adapters."""

from .anthropic import AnthropicAdapter
from .openai_compatible import OpenAICompatibleAdapter

__all__ = ["AnthropicAdapter", "OpenAICompatibleAdapter"]
