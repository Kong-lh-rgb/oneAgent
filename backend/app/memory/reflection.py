"""正常 Agent Run 结束后的普通长期记忆决策。"""

from __future__ import annotations

import asyncio
import json
import time

from app.models.registry import ModelAdapterRegistry
from app.models.types import Message, MessageRole, ModelRequest, ModelUsage

from .reflection_models import (
    MemoryReflectionConfig,
    MemoryReflectionInput,
    MemoryReflectionProposal,
    ReflectionDecision,
)

_REFLECTION_PROMPT = """You are Vesta's post-run long-term memory reflector.

The main Agent has already completed the user's task. Do not answer the user,
continue the task, call tools, change Task, modify Core Memory, or create Skills.
Decide whether this completed run produced exactly one durable ordinary long-term
memory delta. Ordinary memory is sparse and CREATE should grow slowly. Default to
none only when there is no durable delta.

Do not store current task progress, pending steps, temporary constraints, raw tool
output, one-off facts, stable Core identity/preferences, or reusable procedures.
Ordinary memory is for important historical decisions, durable project direction
changes, and background that may need to be recalled in a later session.

For update, only replace an existing memory listed in recalled_memory_ids. Those
IDs prove the main Agent successfully read the full memory during this run. If
only an Index cue is available, return none instead of guessing or erasing details.

Treat a current user's explicit statement that a project decision or rule has
been finalized, completed, corrected, or extended as durable evidence. It does
not require a code edit or file mutation in this run. When such evidence adds a
durable rule to a recalled memory's existing topic, prefer UPDATE over CREATE or
NONE, even when the new rule does not contradict the old content. Do not confuse
"the run changed files" with "the run learned durable project knowledge".

Preserve the recalled memory's still-valid facts when updating. Also preserve
material negations, rejected alternatives, replacement relationships, numeric
limits, and safety constraints from the current evidence. Do not invent a
finalized decision from proposals, speculation, or the assistant's own claims.

Return strict JSON and no markdown fence:
{"action":"none|create|update","memory_id":null,"title":null,
"summary":null,"content":null,"reason":"..."}

CREATE requires title, summary, content. UPDATE requires memory_id plus the
complete replacement title, summary, and content so the recall cue stays aligned
with the body. NONE must leave all mutation fields null."""


class PostRunMemoryReflector:
    """调用可独立配置的模型，只生成普通 Memory 单动作决策。"""

    def __init__(
        self,
        registry: ModelAdapterRegistry,
        *,
        config: MemoryReflectionConfig | None = None,
        default_provider: str | None = None,
        default_model: str | None = None,
    ) -> None:
        self._registry = registry
        self.config = config or MemoryReflectionConfig()
        self._default_provider = default_provider
        self._default_model = default_model

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    @property
    def provider_hint(self) -> str | None:
        return self.config.provider or self._default_provider

    @property
    def model_hint(self) -> str | None:
        if self.config.model is not None:
            return self.config.model
        if self.config.provider is None:
            return self._default_model
        try:
            return self._registry.get(self.config.provider).default_model
        except Exception:
            return None

    async def decide(
        self,
        reflection_input: MemoryReflectionInput,
    ) -> MemoryReflectionProposal:
        """生成严格决策；所有模型与解析失败均转成隔离结果。"""

        if not self.config.enabled:
            return MemoryReflectionProposal()
        started = time.perf_counter()
        usage = ModelUsage()
        provider = self.provider_hint
        model = self.model_hint
        input_json = json.dumps(
            reflection_input.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        raw_output: str | None = None
        try:
            adapter = self._registry.get(provider)
            provider = adapter.provider
            if self.config.model is not None:
                model = self.config.model
            elif self.config.provider is not None:
                model = adapter.default_model
            else:
                model = self._default_model or adapter.default_model
            request = ModelRequest(
                messages=(
                    Message(role=MessageRole.SYSTEM, content=_REFLECTION_PROMPT),
                    Message(
                        role=MessageRole.USER,
                        content=input_json,
                    ),
                ),
                model=model,
                temperature=self.config.temperature,
                max_output_tokens=self.config.max_output_tokens,
            )
            async with asyncio.timeout(self.config.timeout_seconds):
                response = await adapter.complete(request)
            usage = response.usage
            raw_output = response.message.content
            if not raw_output:
                raise ValueError("reflection model returned empty content")
            decision = ReflectionDecision.model_validate_json(
                _strip_code_fence(raw_output)
            )
            return MemoryReflectionProposal(
                decision=decision,
                provider=provider,
                model=model,
                duration_ms=(time.perf_counter() - started) * 1000,
                usage=usage,
                input_json=(input_json if self.config.capture_raw_io else None),
                raw_output=(raw_output if self.config.capture_raw_io else None),
            )
        except Exception as exc:
            return MemoryReflectionProposal(
                provider=provider,
                model=model,
                duration_ms=(time.perf_counter() - started) * 1000,
                usage=usage,
                error=f"{type(exc).__name__}: {exc}",
                input_json=(input_json if self.config.capture_raw_io else None),
                raw_output=(raw_output if self.config.capture_raw_io else None),
            )


def _strip_code_fence(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        return "\n".join(lines[1:-1]).strip()
    return stripped


__all__ = ["PostRunMemoryReflector"]
