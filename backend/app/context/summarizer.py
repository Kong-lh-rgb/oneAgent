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

# 关闭 reasoning 的 extra_body；仅发送给实测支持该字段的 Provider。
_DISABLE_THINKING_BODY = {"thinking": {"type": "disabled"}}
_REASONING_DISABLE_PROVIDERS = frozenset({ModelProvider.DEEPSEEK})

_SUMMARY_SYSTEM_PROMPT = """你是会话压缩器，把旧摘要和新增历史合并成紧凑结构化摘要。

要求：
- 只输出一个 JSON 对象，不要输出 Markdown、解释或任何思考过程；
- 只能保留输入中明确存在的信息，禁止补充、推断或编造事实；
- 只保留后续继续任务需要的信息，删除重复与冗余内容；
- 每个数组最多 5 条，每条不超过 80 个中文字符；
- 没有内容的字段使用 null 或空数组；
- 摘要必须明显短于输入历史。"""


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
        disable_reasoning: bool | None = None,
    ) -> None:
        if max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be greater than zero")
        self._registry = registry
        self._provider = provider
        self._model = model
        self._max_output_tokens = max_output_tokens
        # None 表示自动：仅对支持关闭 reasoning 的 Provider 生效。
        self._disable_reasoning = disable_reasoning

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
                extra_body=(
                    _DISABLE_THINKING_BODY
                    if _disable_reasoning(
                        self._disable_reasoning,
                        self._provider,
                    )
                    else {}
                ),
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


def _disable_reasoning(
    requested: bool | None,
    provider: ModelProvider | str | None,
) -> bool:
    """决定本次摘要请求是否携带关闭 reasoning 的 extra_body。

    None（自动）时仅对实测支持该字段的 Provider 生效；显式 bool 强制覆盖。
    """

    if requested is not None:
        return requested
    if provider is None:
        return False
    normalized = (
        provider.value if isinstance(provider, ModelProvider) else provider
    )
    return normalized in {p.value for p in _REASONING_DISABLE_PROVIDERS}


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
