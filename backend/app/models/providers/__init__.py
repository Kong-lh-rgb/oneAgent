"""内置模型提供商适配器。"""

from .anthropic import AnthropicAdapter
from .openai_compatible import OpenAICompatibleAdapter

__all__ = ["AnthropicAdapter", "OpenAICompatibleAdapter"]
