"""第一阶段：Completed Task Pattern Mining。

只消费轻量 ``TaskCard``（不读取完整对话 / Trace），输出结构化
``TaskPatternCluster`` 列表（允许为空）。不生成 SKILL.md。
"""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field

from app.models.registry import ModelAdapterRegistry
from app.models.types import ModelUsage

from ._call import ModelCallResult, call_model, parse_strict_json
from .config import SkillLearningSettings
from .models import PatternMiningResult, TaskCard, TaskPatternCluster
from .prompts import _PATTERN_MINING_PROMPT


class PatternMiningOutcome(BaseModel):
    """一次 Pattern Mining 的结果；失败通过 error 表达并隔离。"""

    model_config = ConfigDict(extra="forbid")

    clusters: tuple[TaskPatternCluster, ...] = ()
    provider: str | None = None
    model: str | None = None
    duration_ms: float = 0.0
    usage: ModelUsage = Field(default_factory=ModelUsage)
    error: str | None = None


class TaskPatternMiner:
    """判断一组 Completed Task 中是否存在可复用的相似任务类型。"""

    def __init__(
        self,
        registry: ModelAdapterRegistry,
        *,
        settings: SkillLearningSettings,
        default_provider: str | None = None,
        default_model: str | None = None,
    ) -> None:
        self._registry = registry
        self.settings = settings
        self._default_provider = default_provider
        self._default_model = default_model

    async def mine(
        self,
        cards: tuple[TaskCard, ...],
    ) -> PatternMiningOutcome:
        """对 TaskCard 批次做聚类判断；模型失败返回空 clusters + error。"""

        if not cards:
            return PatternMiningOutcome()
        user_content = json.dumps(
            [card.model_dump(mode="json") for card in cards],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        result: ModelCallResult = await call_model(
            self._registry,
            system_prompt=_PATTERN_MINING_PROMPT,
            user_content=user_content,
            settings=self.settings,
            default_provider=self._default_provider,
            default_model=self._default_model,
        )
        if not result.ok:
            return PatternMiningOutcome(
                provider=result.provider,
                model=result.model,
                duration_ms=result.duration_ms,
                usage=result.usage,
                error=result.error,
            )
        payload = parse_strict_json(result.raw_output or "")
        if payload is None:
            return PatternMiningOutcome(
                provider=result.provider,
                model=result.model,
                duration_ms=result.duration_ms,
                usage=result.usage,
                error="pattern mining returned non-JSON output",
            )
        try:
            parsed = PatternMiningResult.model_validate(payload)
        except Exception as exc:
            return PatternMiningOutcome(
                provider=result.provider,
                model=result.model,
                duration_ms=result.duration_ms,
                usage=result.usage,
                error=f"invalid pattern mining schema: {type(exc).__name__}: {exc}",
            )
        valid_ids = {card.task_id for card in cards}
        clusters = tuple(
            cluster
            for cluster in parsed.clusters
            if len(cluster.task_ids) >= self.settings.skill_learning_min_cluster_size
            and set(cluster.task_ids).issubset(valid_ids)
        )
        return PatternMiningOutcome(
            clusters=clusters,
            provider=result.provider,
            model=result.model,
            duration_ms=result.duration_ms,
            usage=result.usage,
        )


__all__ = ["PatternMiningOutcome", "TaskPatternMiner"]
