"""Agent 一次 run() 的完整运行结果。

包含最终回复、执行步数、停止原因、工具调用过程（按轮分组）、
token 用量与结构化错误，供上层（API / 前端 / 日志）使用。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.models.types import Message, ModelUsage, ToolCall, ToolResult


class AgentStopReason(StrEnum):
    """Agent 循环停止的原因。"""

    FINAL_ANSWER = "final_answer"
    CONTEXT_ERROR = "context_error"
    MODEL_ERROR = "model_error"
    REPEATED_TOOL_CALL = "repeated_tool_call"
    MAX_STEPS = "max_steps"


class AgentError(BaseModel):
    """结构化的运行错误（可序列化）。"""

    model_config = ConfigDict(extra="forbid")

    type: str
    message: str


class ToolCallRecord(BaseModel):
    """单次工具调用的执行记录（工具调用过程的最小单元）。"""

    model_config = ConfigDict(extra="forbid")

    round_index: int
    tool_call: ToolCall
    result: ToolResult


class ToolRound(BaseModel):
    """一轮模型响应中发起的一组工具调用。"""

    model_config = ConfigDict(extra="forbid")

    round_index: int
    assistant_message: Message
    records: tuple[ToolCallRecord, ...] = ()


class AgentResult(BaseModel):
    """Agent 一次 run() 的完整结果。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    final_message: Message
    messages: tuple[Message, ...]
    steps: int = 0
    stop_reason: AgentStopReason
    tool_rounds: tuple[ToolRound, ...] = ()
    tool_calls: tuple[ToolCallRecord, ...] = ()
    usage: ModelUsage = Field(default_factory=ModelUsage)
    error: AgentError | None = None

    @property
    def ok(self) -> bool:
        """是否正常得到最终答案。"""
        return self.stop_reason is AgentStopReason.FINAL_ANSWER

    @property
    def content(self) -> str | None:
        """最终回复的文本内容。"""
        return self.final_message.content

    @property
    def role(self) -> str:
        return self.final_message.role.value
