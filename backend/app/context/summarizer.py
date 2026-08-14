"""滚动摘要生成接口及模型实现。"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from app.models.registry import ModelAdapterRegistry
from app.models.types import (
    Message,
    MessageRole,
    ModelProvider,
    ModelRequest,
    ModelUsage,
)

from .summary import (
    RollingConversationSummary,
    SummaryGenerationResult,
)

# 关闭 reasoning 的 extra_body；仅发送给实测支持该字段的 Provider。
_DISABLE_THINKING_BODY = {"thinking": {"type": "disabled"}}
_REASONING_DISABLE_PROVIDERS = frozenset({ModelProvider.DEEPSEEK})
_MAX_OBJECTIVE_CHARS = 160
_PREFERRED_ENTRIES_PER_FIELD = 5
_MAX_ENTRIES_PER_FIELD = 8
_MAX_ENTRY_CHARS = 80
_MAX_SUMMARY_CONTENT_CHARS = 1_200

_SUMMARY_SYSTEM_PROMPT = """你是会话压缩器，把旧摘要和新增历史合并成紧凑结构化摘要。

要求：
- 只输出一个 JSON 对象，不要输出 Markdown、解释或任何思考过程；
- 只能保留输入中明确存在的信息，禁止补充、推断或编造事实；
- 只保留后续继续任务需要的信息，删除重复与冗余内容；
- 不要推测或复制外部 Task Snapshot 的步骤状态，Task 是独立事实源；
- 每个数组最多 5 条，每条不超过 80 个中文字符；
- 当前目标不超过 160 个字符，全部字段内容合计不超过 1200 个字符；
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

    async def retry_compact(
        self,
        previous_summary: RollingConversationSummary | None,
        messages: Sequence[Message],
        *,
        reason: str,
    ) -> SummaryGenerationResult:
        """首次摘要失败后的唯一重试入口；默认复用原摘要实现。"""

        return await self.summarize(previous_summary, messages)


class SummaryGenerationError(ValueError):
    """摘要响应不合格，并携带该次响应已经产生的 Token 用量。"""

    def __init__(self, message: str, *, usage: ModelUsage) -> None:
        super().__init__(message)
        self.usage = usage


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
        return await self._summarize(
            previous_summary,
            messages,
            retry_reason=None,
        )

    async def retry_compact(
        self,
        previous_summary: RollingConversationSummary | None,
        messages: Sequence[Message],
        *,
        reason: str,
    ) -> SummaryGenerationResult:
        """用更严格提示执行一次压缩重试。"""

        return await self._summarize(
            previous_summary,
            messages,
            retry_reason=reason,
        )

    async def _summarize(
        self,
        previous_summary: RollingConversationSummary | None,
        messages: Sequence[Message],
        *,
        retry_reason: str | None,
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
            "output_schema": _compact_summary_schema(),
        }
        system_prompt = _SUMMARY_SYSTEM_PROMPT
        if retry_reason:
            compact_reason = " ".join(retry_reason.split())[:240]
            system_prompt += (
                "\n\n上一次摘要不合格："
                f"{compact_reason}。这是唯一重试机会，必须输出更短的合法 JSON；"
                "优先保留用户约束、关键决定、当前状态和未完成事项；"
                "不要推测或复制外部 Task Snapshot 的步骤状态。"
            )
        response = await adapter.complete(
            ModelRequest(
                model=self._model or adapter.default_model,
                messages=(
                    Message(
                        role=MessageRole.SYSTEM,
                        content=system_prompt,
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
            raise SummaryGenerationError(
                "summary model returned empty content",
                usage=response.usage,
            )
        try:
            summary = RollingConversationSummary.model_validate(
                _parse_json_object(content)
            )
            _validate_compact_summary(summary)
        except Exception as exc:
            if isinstance(exc, SummaryGenerationError):
                raise
            raise SummaryGenerationError(
                f"invalid summary output: {type(exc).__name__}: {exc}",
                usage=response.usage,
            ) from exc
        return SummaryGenerationResult(summary=summary, usage=response.usage)


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


def _validate_compact_summary(summary: RollingConversationSummary) -> None:
    """对模型摘要执行硬限制，不能只依赖 Prompt 软约束。"""

    if (
        summary.current_objective
        and len(summary.current_objective) > _MAX_OBJECTIVE_CHARS
    ):
        raise ValueError(
            f"current_objective exceeds {_MAX_OBJECTIVE_CHARS} characters"
        )
    entry_fields = (
        "user_constraints",
        "key_decisions",
        "completed_work",
        "current_state",
        "pending_work",
        "important_facts",
    )
    total_chars = len(summary.current_objective or "")
    for field_name in entry_fields:
        entries = getattr(summary, field_name)
        if len(entries) > _MAX_ENTRIES_PER_FIELD:
            raise ValueError(
                f"{field_name} exceeds {_MAX_ENTRIES_PER_FIELD} entries"
            )
        for entry in entries:
            if len(entry) > _MAX_ENTRY_CHARS:
                raise ValueError(
                    f"{field_name} entry exceeds {_MAX_ENTRY_CHARS} characters"
                )
            total_chars += len(entry)
    if total_chars > _MAX_SUMMARY_CONTENT_CHARS:
        raise ValueError(
            f"summary content exceeds {_MAX_SUMMARY_CONTENT_CHARS} characters"
        )


def _compact_summary_schema() -> dict[str, Any]:
    """在提示给模型的 JSON Schema 中声明紧凑输出硬边界。"""

    schema = RollingConversationSummary.model_json_schema()
    properties = schema.get("properties", {})
    objective = properties.get("current_objective")
    if isinstance(objective, dict):
        objective["maxLength"] = _MAX_OBJECTIVE_CHARS
    for field_name in (
        "user_constraints",
        "key_decisions",
        "completed_work",
        "current_state",
        "pending_work",
        "important_facts",
    ):
        field_schema = properties.get(field_name)
        if not isinstance(field_schema, dict):
            continue
        field_schema["maxItems"] = _MAX_ENTRIES_PER_FIELD
        item_schema = field_schema.get("items")
        if isinstance(item_schema, dict):
            item_schema["maxLength"] = _MAX_ENTRY_CHARS
    schema["description"] = (
        f"Prefer at most {_PREFERRED_ENTRIES_PER_FIELD} entries per array; "
        f"hard maximum {_MAX_ENTRIES_PER_FIELD}."
    )
    return schema


__all__ = [
    "ContextSummarizer",
    "ModelContextSummarizer",
    "SummaryGenerationError",
]
