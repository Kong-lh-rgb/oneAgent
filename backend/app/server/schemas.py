"""Agent Server 的请求模型（响应直接序列化现有领域模型，不另造 schema）。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CreateConversationRequest(BaseModel):
    """创建一个新会话（可选标题）。"""

    model_config = ConfigDict(extra="forbid")

    title: str | None = None


class SendMessageRequest(BaseModel):
    """向一个会话投递一条手动输入。"""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1)


class CreateAutomationRequest(BaseModel):
    """创建一条结构化 Automation（不做自然语言时间解析）。

    时间字段与 ``app.automation.tools.build_schedule_and_next`` 保持一致。
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    kind: str
    run_at: str | None = None
    interval_seconds: float | None = None
    cron_expr: str | None = None
    timezone: str | None = None
    conversation_id: str | None = None


__all__ = [
    "CreateAutomationRequest",
    "CreateConversationRequest",
    "SendMessageRequest",
]
