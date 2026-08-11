"""正常 Agent Run 结束后的普通长期记忆反思与单动作写入。"""

from __future__ import annotations

import asyncio
import json
import time

from app.models.registry import ModelAdapterRegistry
from app.models.types import Message, MessageRole, ModelRequest, ModelUsage

from .manager import MemoryManager
from .reflection_models import (
    MemoryReflectionConfig,
    MemoryReflectionInput,
    MemoryReflectionOutcome,
    ReflectionAction,
    ReflectionDecision,
)

_REFLECTION_PROMPT = """You are OneAgent's post-run long-term memory reflector.

The main Agent has already completed the user's task. Do not answer the user,
continue the task, call tools, change Task, modify Core Memory, or create Skills.
Decide whether this completed run produced exactly one durable ordinary long-term
memory delta. Default to none. Ordinary memory is sparse and should grow slowly.

Do not store current task progress, pending steps, temporary constraints, raw tool
output, one-off facts, stable Core identity/preferences, or reusable procedures.
Ordinary memory is for important historical decisions, durable project direction
changes, and background that may need to be recalled in a later session.

For update, only replace an existing memory listed in recalled_memory_ids. Those
IDs prove the main Agent successfully read the full memory during this run. If
only an Index cue is available, return none instead of guessing or erasing details.

Return strict JSON and no markdown fence:
{"action":"none|create|update","memory_id":null,"title":null,
"summary":null,"content":null,"reason":"..."}

CREATE requires title, summary, content. UPDATE requires memory_id and complete
replacement content. NONE must leave all mutation fields null."""


class PostRunMemoryReflector:
    """调用可独立配置的模型，并通过 MemoryManager 应用至多一个 mutation。"""

    def __init__(
        self,
        registry: ModelAdapterRegistry,
        manager: MemoryManager,
        *,
        config: MemoryReflectionConfig | None = None,
        default_provider: str | None = None,
        default_model: str | None = None,
    ) -> None:
        self._registry = registry
        self._manager = manager
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

    async def run(
        self,
        reflection_input: MemoryReflectionInput,
    ) -> MemoryReflectionOutcome:
        """执行一次同步 Reflection；所有失败均转成隔离后的 Outcome。"""

        if not self.config.enabled:
            return MemoryReflectionOutcome(triggered=False)
        started = time.perf_counter()
        usage = ModelUsage()
        provider = self.provider_hint
        model = self.model_hint
        action: ReflectionAction | None = None
        memory_id: str | None = None
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
                        content=json.dumps(
                            reflection_input.model_dump(mode="json"),
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    ),
                ),
                model=model,
                temperature=self.config.temperature,
                max_output_tokens=self.config.max_output_tokens,
            )
            async with asyncio.timeout(self.config.timeout_seconds):
                response = await adapter.complete(request)
            usage = response.usage
            if not response.message.content:
                raise ValueError("reflection model returned empty content")
            decision = ReflectionDecision.model_validate_json(
                _strip_code_fence(response.message.content)
            )
            action = decision.action
            if action is ReflectionAction.UPDATE:
                memory_id = decision.memory_id
            if (
                action is ReflectionAction.UPDATE
                and decision.memory_id not in reflection_input.recalled_memory_ids
            ):
                raise ValueError(
                    "reflection update requires memory.read success in current run"
                )
            memory_id = await self._apply(decision)
            maintenance_required = await self._manager.maintenance_required()
            candidate_ids: tuple[str, ...] = ()
            if maintenance_required:
                candidates = await self._manager.retention_candidates()
                candidate_ids = tuple(item.id for item in candidates)
            return MemoryReflectionOutcome(
                triggered=True,
                action=action,
                provider=provider,
                model=model,
                duration_ms=(time.perf_counter() - started) * 1000,
                usage=usage,
                memory_id=memory_id,
                maintenance_required=maintenance_required,
                retention_candidate_ids=candidate_ids,
            )
        except Exception as exc:
            return MemoryReflectionOutcome(
                triggered=True,
                action=action,
                provider=provider,
                model=model,
                duration_ms=(time.perf_counter() - started) * 1000,
                usage=usage,
                error=f"{type(exc).__name__}: {exc}",
                memory_id=memory_id,
            )

    async def _apply(self, decision: ReflectionDecision) -> str | None:
        if decision.action is ReflectionAction.NONE:
            return None
        if decision.action is ReflectionAction.CREATE:
            record = await self._manager.create(
                title=decision.title or "",
                summary=decision.summary or "",
                content=decision.content or "",
            )
            return record.id
        record = await self._manager.update(
            decision.memory_id or "",
            content=decision.content or "",
            reason=decision.reason,
        )
        return record.id


def _strip_code_fence(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        return "\n".join(lines[1:-1]).strip()
    return stripped


__all__ = ["PostRunMemoryReflector"]
