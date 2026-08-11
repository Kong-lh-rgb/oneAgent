"""使用独立小模型为容量维护选择 archive/defer 单动作。"""

from __future__ import annotations

import asyncio
import json
import time

from app.models.registry import ModelAdapterRegistry
from app.models.types import Message, MessageRole, ModelRequest, ModelUsage

from .maintenance_models import (
    MemoryMaintenanceConfig,
    MemoryMaintenanceDecision,
    MemoryMaintenanceInput,
    MemoryMaintenanceProposal,
)

_MAINTENANCE_PROMPT = """You are OneAgent's long-term memory capacity maintainer.

The active memory store is full or already over capacity. Review only the supplied
candidates. Choose at most one recoverable ARCHIVE action when a candidate is
obsolete, duplicated, superseded, or no longer useful across sessions. Otherwise
DEFER. Never invent an ID, modify content, merge memories, or answer the user.

ARCHIVE moves a Markdown record to a recoverable archive; it is not deletion.
Prefer DEFER when the candidates remain independently valuable or evidence is
insufficient. Return strict JSON and no markdown fence:
{"action":"archive|defer","memory_id":null,"reason":"..."}

ARCHIVE requires one ID from candidates. DEFER requires memory_id=null."""


class MemoryMaintenanceReflector:
    """生成容量维护决策，不直接修改 Memory Store。"""

    def __init__(
        self,
        registry: ModelAdapterRegistry,
        *,
        config: MemoryMaintenanceConfig | None = None,
        default_provider: str | None = None,
        default_model: str | None = None,
    ) -> None:
        self._registry = registry
        self.config = config or MemoryMaintenanceConfig()
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
        maintenance_input: MemoryMaintenanceInput,
    ) -> MemoryMaintenanceProposal:
        """生成严格维护决策，并隔离模型、超时与解析错误。"""

        if not self.config.enabled:
            return MemoryMaintenanceProposal()
        started = time.perf_counter()
        usage = ModelUsage()
        provider = self.provider_hint
        model = self.model_hint
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
                    Message(role=MessageRole.SYSTEM, content=_MAINTENANCE_PROMPT),
                    Message(
                        role=MessageRole.USER,
                        content=json.dumps(
                            maintenance_input.model_dump(mode="json"),
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
                raise ValueError("maintenance model returned empty content")
            decision = MemoryMaintenanceDecision.model_validate_json(
                _strip_code_fence(response.message.content)
            )
            return MemoryMaintenanceProposal(
                decision=decision,
                provider=provider,
                model=model,
                duration_ms=(time.perf_counter() - started) * 1000,
                usage=usage,
            )
        except Exception as exc:
            return MemoryMaintenanceProposal(
                provider=provider,
                model=model,
                duration_ms=(time.perf_counter() - started) * 1000,
                usage=usage,
                error=f"{type(exc).__name__}: {exc}",
            )


def _strip_code_fence(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        return "\n".join(lines[1:-1]).strip()
    return stripped


__all__ = ["MemoryMaintenanceReflector"]
