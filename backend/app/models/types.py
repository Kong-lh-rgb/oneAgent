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
    # Provider Adapter 可临时承载推理内容；AgentRuntime 会在事件与持久化前清除，
    # 不向用户展示，也不随后续请求回传给模型。
    reasoning: str | None = None


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


class ToolUiScope(StrEnum):
    """工具审批的展示落点（声明式路由，前端不靠 tool_name 前缀猜测）。

    - SANDBOX: 作用于 Vesta 沙盒 / 宿主（shell、http…），审批属于对话工作流，
      永远进 Chat。
    - DESKTOP: 作用于用户真实桌面（computer…），审批跟随用户注意力：
      主窗口聚焦进 Chat，否则进 Floating Window。
    """

    SANDBOX = "sandbox"
    DESKTOP = "desktop"


class ToolDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )
    strict: bool | None = None
    permission: ToolPermission = ToolPermission.ALLOWED
    ui_scope: ToolUiScope = ToolUiScope.SANDBOX


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
    """一次或多次模型调用的用量。

    ``input_tokens`` 表示 Provider 处理的全部输入（包含缓存命中）；缓存细分
    无法从响应确认时保持 ``None``，不能把“未知”伪装成 0。
    """

    model_config = ConfigDict(extra="allow")

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_input_tokens: int | None = Field(default=None, ge=0)
    uncached_input_tokens: int | None = Field(default=None, ge=0)
    cache_read_input_tokens: int | None = Field(default=None, ge=0)
    cache_write_input_tokens: int | None = Field(default=None, ge=0)
    model_calls: int = Field(default=0, ge=0)


def add_model_usage(left: ModelUsage, right: ModelUsage) -> ModelUsage:
    """聚合 Usage，并保留缓存字段的“未知”语义。"""

    left_has_usage = _has_model_usage(left)
    right_has_usage = _has_model_usage(right)

    def add_optional(left_value: int | None, right_value: int | None) -> int | None:
        if not left_has_usage:
            return right_value
        if not right_has_usage:
            return left_value
        if left_value is None or right_value is None:
            return None
        return left_value + right_value

    return ModelUsage(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        total_tokens=left.total_tokens + right.total_tokens,
        cached_input_tokens=add_optional(
            left.cached_input_tokens,
            right.cached_input_tokens,
        ),
        uncached_input_tokens=add_optional(
            left.uncached_input_tokens,
            right.uncached_input_tokens,
        ),
        cache_read_input_tokens=add_optional(
            left.cache_read_input_tokens,
            right.cache_read_input_tokens,
        ),
        cache_write_input_tokens=add_optional(
            left.cache_write_input_tokens,
            right.cache_write_input_tokens,
        ),
        model_calls=left.model_calls + right.model_calls,
    )


def _has_model_usage(usage: ModelUsage) -> bool:
    return bool(
        usage.input_tokens
        or usage.output_tokens
        or usage.total_tokens
        or usage.cached_input_tokens is not None
        or usage.uncached_input_tokens is not None
        or usage.cache_read_input_tokens is not None
        or usage.cache_write_input_tokens is not None
        or usage.model_calls
    )


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
