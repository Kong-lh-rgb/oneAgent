"""从运行观察中选择性提取长期记忆。"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.models.registry import ModelAdapterRegistry
from app.models.types import Message, MessageRole, ModelRequest

from .models import MemoryDraft, MemorySource, MemoryStatus, MemoryType

_EXTRACTOR_PROMPT = """你是长期记忆提取器。不要总结对话，只保存未来可能改变 Agent
决策或执行方式的信息。忽略寒暄、感谢、普通回答、原始工具输出和一次性细节。
只提取 FACT、EPISODE、PROCEDURE；最多 3 条。明确用户事实/约束可设 active，模型推断
只能设 candidate。FACT 必须提供稳定的英文点号 key。严格输出 JSON：
{"memories":[{"memory_type":"fact|episode|procedure","key":null,
"content":"...","status":"candidate|active","importance":0.5,"confidence":0.8}]}
没有值得保存的信息时输出 {"memories":[]}。"""


class MemoryExtractor(ABC):
    @abstractmethod
    async def extract(
        self,
        observation: str,
        *,
        namespace: str,
        source: MemorySource,
    ) -> tuple[MemoryDraft, ...]:
        """返回零到三条候选记忆。"""


class RuleMemoryFilter:
    """在调用提取模型前过滤明显无记忆价值的文本。"""

    _signals = (
        "记住",
        "以后",
        "偏好",
        "必须",
        "决定",
        "约束",
        "失败",
        "成功",
        "解决",
        "remember",
        "prefer",
        "must",
        "failed",
        "fixed",
    )

    def should_extract(self, observation: str) -> bool:
        normalized = observation.strip().lower()
        return len(normalized) >= 12 and any(
            signal in normalized for signal in self._signals
        )

    def allows_direct_activation(self, user_text: str) -> bool:
        """只有用户原文中的明确记忆指令允许跳过候选态。"""

        normalized = user_text.strip().lower()
        explicit_signals = ("记住", "以后", "始终", "必须", "remember", "always")
        return any(signal in normalized for signal in explicit_signals)


class ModelMemoryExtractor(MemoryExtractor):
    """复用模型适配层产生结构化记忆候选。"""

    def __init__(
        self,
        registry: ModelAdapterRegistry,
        *,
        provider: str | None = None,
        model: str | None = None,
        max_output_tokens: int = 1200,
    ) -> None:
        self._registry = registry
        self._provider = provider
        self._model = model
        self._max_output_tokens = max_output_tokens

    async def extract(
        self,
        observation: str,
        *,
        namespace: str,
        source: MemorySource,
    ) -> tuple[MemoryDraft, ...]:
        adapter = self._registry.get(self._provider)
        response = await adapter.complete(
            ModelRequest(
                messages=(
                    Message(role=MessageRole.SYSTEM, content=_EXTRACTOR_PROMPT),
                    Message(role=MessageRole.USER, content=observation[:12_000]),
                ),
                model=self._model or adapter.default_model,
                max_output_tokens=self._max_output_tokens,
            )
        )
        if not response.message.content:
            return ()
        try:
            payload = _ExtractionPayload.model_validate_json(
                _strip_code_fence(response.message.content)
            )
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"memory extractor returned invalid JSON: {exc}") from exc
        return tuple(
            MemoryDraft(
                namespace=namespace,
                memory_type=item.memory_type,
                key=item.key,
                content=item.content,
                status=item.status,
                importance=item.importance,
                confidence=item.confidence,
                source=source,
            )
            for item in payload.memories[:3]
        )


class _ExtractedMemory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_type: MemoryType
    key: str | None = None
    content: str
    status: MemoryStatus
    importance: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)


class _ExtractionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memories: tuple[_ExtractedMemory, ...] = ()


def _strip_code_fence(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        return "\n".join(lines[1:-1]).strip()
    return stripped


__all__ = [
    "MemoryExtractor",
    "ModelMemoryExtractor",
    "RuleMemoryFilter",
]
