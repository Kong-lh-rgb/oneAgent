"""滚动摘要生成接口及模型实现。"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from app.models.registry import ModelAdapterRegistry
from app.models.types import Message, MessageRole, ModelProvider, ModelRequest

from .summary import (
    RollingConversationSummary,
    SummaryGenerationResult,
)

_SUMMARY_SYSTEM_PROMPT = """你是会话压缩器。
请把旧摘要和新增历史合并成一个完整的结构化摘要。
只能保留输入中明确存在的信息，禁止补充、推断或编造事实。
必须保留仍有效的用户约束、关键决定、当前状态和未完成事项。
只输出一个 JSON 对象，不要输出 Markdown 或解释。"""


class ContextSummarizer(ABC):
    """把上一版摘要和一组旧对话合并成新摘要。"""

    @abstractmethod
    async def summarize(
        self,
        previous_summary: RollingConversationSummary | None,
        messages: Sequence[Message],
    ) -> SummaryGenerationResult:
        """返回完整新摘要；失败时抛出异常且调用方不得删除原消息。"""


class ModelContextSummarizer(ContextSummarizer):
    """使用已配置模型生成严格 JSON 滚动摘要。"""

    def __init__(
        self,
        registry: ModelAdapterRegistry,
        *,
        provider: ModelProvider | str | None = None,
        model: str | None = None,
        max_output_tokens: int = 1_024,
    ) -> None:
        if max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be greater than zero")
        self._registry = registry
        self._provider = provider
        self._model = model
        self._max_output_tokens = max_output_tokens

    async def summarize(
        self,
        previous_summary: RollingConversationSummary | None,
        messages: Sequence[Message],
    ) -> SummaryGenerationResult:
        if not messages:
            raise ValueError("summary messages cannot be empty")
        adapter = self._registry.get(self._provider)
        payload = {
            "previous_summary": (
                previous_summary.model_dump(mode="json")
                if previous_summary is not None
                else None
            ),
            "new_history": [
                {
                    "role": message.role.value,
                    "content": message.content or "",
                }
                for message in messages
            ],
            "output_schema": RollingConversationSummary.model_json_schema(),
        }
        response = await adapter.complete(
            ModelRequest(
                model=self._model or adapter.default_model,
                messages=(
                    Message(
                        role=MessageRole.SYSTEM,
                        content=_SUMMARY_SYSTEM_PROMPT,
                    ),
                    Message(
                        role=MessageRole.USER,
                        content=json.dumps(
                            payload,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    ),
                ),
                tools=(),
                max_output_tokens=self._max_output_tokens,
            )
        )
        content = response.message.content
        if not content:
            raise ValueError("summary model returned empty content")
        return SummaryGenerationResult(
            summary=RollingConversationSummary.model_validate(
                _parse_json_object(content)
            ),
            usage=response.usage,
        )


def _parse_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("summary model output must be a JSON object")
    return parsed


__all__ = ["ContextSummarizer", "ModelContextSummarizer"]
