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


class AgentMode(StrEnum):
    """一次 Agent 执行的模式（输入语义，不是 Run 生命周期状态）。

    - NORMAL：默认模式，模型自行判断是否需要 Task；
    - PLAN：只读 / 规划模式，只分析调查并形成一个 PENDING Task，不修改环境。
    """

    NORMAL = "normal"
    PLAN = "plan"


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


class ToolPermission(StrEnum):
    """工具的执行权限档位。

    - ALLOWED: 模型可直接调用，无需额外审核。
    - HUMAN_APPROVAL: 模型可申请调用，但执行前必须经过人工审核。
    - FORBIDDEN: 严格禁止模型执行；工具可注册但不向模型暴露。
    """

    ALLOWED = "allowed"
    HUMAN_APPROVAL = "human_approval"
    FORBIDDEN = "forbidden"

    def model_visible(self) -> bool:
        """是否应该被暴露给模型（禁止档不暴露）。"""
        return self is not ToolPermission.FORBIDDEN


class ToolDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )
    strict: bool | None = None
    permission: ToolPermission = ToolPermission.ALLOWED


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
