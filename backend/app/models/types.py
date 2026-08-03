"""与模型提供商无关的请求和响应类型。"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ModelProvider(StrEnum):
    OPENAI = "openai"
    QWEN = "qwen"
    DEEPSEEK = "deepseek"
    ANTHROPIC = "anthropic"


class ApiStyle(StrEnum):
    RESPONSES = "responses"
    CHAT_COMPLETIONS = "chat_completions"
    ANTHROPIC_MESSAGES = "anthropic_messages"


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    arguments: dict[str, Any] | str = Field(default_factory=dict)


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: MessageRole
    content: str | None = None
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()


class ToolDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )
    strict: bool | None = None


class ToolResult(BaseModel):
    """一次本地工具执行的统一结果。"""

    model_config = ConfigDict(extra="forbid")

    tool_call_id: str
    tool_name: str
    success: bool
    output: str | None = None
    error: str | None = None
    duration_ms: float = Field(ge=0)


class ModelRequest(BaseModel):
    """可转换为任意已配置模型提供商格式的请求。"""

    model_config = ConfigDict(extra="forbid")

    messages: tuple[Message, ...]
    model: str | None = None
    tools: tuple[ToolDefinition, ...] = ()
    tool_choice: str | None = None
    temperature: float | None = None
    max_output_tokens: int | None = Field(default=None, gt=0)
    extra_body: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_messages(self) -> ModelRequest:
        if not self.messages:
            raise ValueError("messages cannot be empty")
        return self


class ModelUsage(BaseModel):
    model_config = ConfigDict(extra="allow")

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class ModelResponse(BaseModel):
    """所有模型适配器统一返回的结果。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    provider: str
    model: str
    message: Message
    finish_reason: str | None = None
    usage: ModelUsage = Field(default_factory=ModelUsage)
    raw: dict[str, Any] | None = None
