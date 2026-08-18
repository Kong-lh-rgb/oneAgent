"""Skill Learning 的模型调用辅助（统一异常隔离 + usage 收集）。"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field

from app.models.registry import ModelAdapterRegistry
from app.models.types import Message, MessageRole, ModelRequest, ModelUsage

from .config import SkillLearningSettings


@dataclass
class ModelCallResult:
    """一次结构化模型调用的结果；模型/解析失败通过 error 表达，不抛异常。"""

    raw_output: str | None = None
    provider: str | None = None
    model: str | None = None
    duration_ms: float = 0.0
    usage: ModelUsage = field(default_factory=ModelUsage)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.raw_output)


async def call_model(
    registry: ModelAdapterRegistry,
    *,
    system_prompt: str,
    user_content: str,
    settings: SkillLearningSettings,
    default_provider: str | None,
    default_model: str | None,
) -> ModelCallResult:
    """调用模型并返回原始输出；所有异常隔离成 error。"""

    started = time.perf_counter()
    usage = ModelUsage()
    provider = settings.skill_learning_provider or default_provider
    model = settings.skill_learning_model or default_model
    raw_output: str | None = None
    try:
        adapter = registry.get(provider)
        resolved_provider = adapter.provider
        if settings.skill_learning_model is not None:
            resolved_model = settings.skill_learning_model
        elif settings.skill_learning_provider is not None:
            resolved_model = adapter.default_model
        else:
            resolved_model = default_model or adapter.default_model
        request = ModelRequest(
            messages=(
                Message(role=MessageRole.SYSTEM, content=system_prompt),
                Message(role=MessageRole.USER, content=user_content),
            ),
            model=resolved_model,
            temperature=settings.skill_learning_temperature,
            max_output_tokens=settings.skill_learning_max_output_tokens,
        )
        async with asyncio.timeout(settings.skill_learning_timeout_seconds):
            response = await adapter.complete(request)
        usage = response.usage
        raw_output = response.message.content
        if not raw_output:
            raise ValueError("skill learning model returned empty content")
        return ModelCallResult(
            raw_output=raw_output,
            provider=resolved_provider,
            model=resolved_model,
            duration_ms=(time.perf_counter() - started) * 1000,
            usage=usage,
        )
    except Exception as exc:
        return ModelCallResult(
            provider=provider,
            model=model,
            duration_ms=(time.perf_counter() - started) * 1000,
            usage=usage,
            error=f"{type(exc).__name__}: {exc}",
        )


def parse_strict_json(raw_output: str) -> dict | None:
    """解析严格 JSON（容忍 markdown fence），失败返回 None。"""

    stripped = raw_output.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        stripped = "\n".join(lines[1:-1]).strip()
    try:
        payload = json.loads(stripped)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


__all__ = ["ModelCallResult", "call_model", "parse_strict_json"]
