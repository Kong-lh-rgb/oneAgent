"""第二阶段：按 Cluster 深挖 Trace 并提炼 Skill Candidate。

Pattern Miner 找到 Cluster 后，Distiller 接收：
- cluster；
- 每个 source task 的压缩执行证据（evidence builder 输出）；
- 每个 source task 的 run_ids；
- 现有 Skill Catalog（name + description，用于 CREATE / UPDATE / NONE 判断）。

输出一个 SkillCandidate（pending），不直接修改正式 Skill。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.registry import ModelAdapterRegistry
from app.models.types import ModelUsage
from app.skills import SkillMetadata

from ._call import ModelCallResult, call_model, parse_strict_json
from .config import SkillLearningSettings
from .models import (
    SkillCandidate,
    SkillCandidateAction,
    SkillCandidateStatus,
    TaskPatternCluster,
)
from .prompts import _DISTILLATION_PROMPT


class _Distilled(BaseModel):
    """模型蒸馏输出的宽松中间结构（随后映射到 SkillCandidate 再严格校验）。"""

    model_config = ConfigDict(extra="forbid")

    action: str
    proposed_name: str | None = None
    description: str | None = None
    reason: str | None = None
    procedure: tuple[str, ...] = ()
    pitfalls: tuple[str, ...] = ()
    verification: tuple[str, ...] = ()
    existing_skill_name: str | None = None

    @field_validator("action")
    @classmethod
    def valid_action(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"none", "create", "update"}:
            raise ValueError(f"invalid action: {value}")
        return normalized

    @field_validator("procedure", "pitfalls", "verification", mode="before")
    @classmethod
    def normalize_list_field(cls, value: object) -> object:
        """真实模型常把列表字段输出为 null 或单个字符串；统一归一化为列表。"""

        if value is None:
            return ()
        if isinstance(value, str):
            return (value,)
        return value


class DistillationOutcome(BaseModel):
    """一次蒸馏的结果；candidate 为空表示 action=none 或失败。"""

    model_config = ConfigDict(extra="forbid")

    candidate: SkillCandidate | None = None
    action: str | None = None
    provider: str | None = None
    model: str | None = None
    duration_ms: float = 0.0
    usage: ModelUsage = Field(default_factory=ModelUsage)
    raw_output: str | None = None
    error: str | None = None


class ProcedureDistiller:
    """从相似 Completed Task + 执行证据中判断并提炼稳定可复用流程。"""

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

    async def distill(
        self,
        cluster: TaskPatternCluster,
        *,
        evidence: dict[str, str],
        run_ids: dict[str, tuple[str, ...]],
        catalog: Sequence[SkillMetadata] = (),
        pending_candidates: Sequence[SkillCandidate] = (),
    ) -> DistillationOutcome:
        """对单个 Cluster 做蒸馏；action=none 返回空 candidate。

        ``pending_candidates`` 用于判断新 pattern 是否已被一个待评审 Candidate
        覆盖，避免重复创建同义 Candidate。
        """

        user_payload: dict[str, Any] = {
            "cluster": cluster.model_dump(mode="json"),
            "evidence": evidence,
            "catalog": [
                {"name": item.name, "description": item.description}
                for item in catalog
            ],
            "pending_candidates": [
                {
                    "id": item.id,
                    "action": item.action.value,
                    "proposed_name": item.proposed_name,
                    "description": item.description,
                    "existing_skill_name": item.existing_skill_name,
                    "reason": item.reason[:300],
                }
                for item in pending_candidates
            ],
        }
        user_content = json.dumps(
            user_payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        result: ModelCallResult = await call_model(
            self._registry,
            system_prompt=_DISTILLATION_PROMPT,
            user_content=user_content,
            settings=self.settings,
            default_provider=self._default_provider,
            default_model=self._default_model,
        )
        if not result.ok:
            return DistillationOutcome(
                provider=result.provider,
                model=result.model,
                duration_ms=result.duration_ms,
                usage=result.usage,
                raw_output=result.raw_output,
                error=result.error,
            )
        payload = parse_strict_json(result.raw_output or "")
        if payload is None:
            return DistillationOutcome(
                provider=result.provider,
                model=result.model,
                duration_ms=result.duration_ms,
                usage=result.usage,
                raw_output=result.raw_output,
                error="distillation returned non-JSON output",
            )
        try:
            distilled = _Distilled.model_validate(payload)
        except Exception as exc:
            return DistillationOutcome(
                provider=result.provider,
                model=result.model,
                duration_ms=result.duration_ms,
                usage=result.usage,
                raw_output=result.raw_output,
                error=f"invalid distillation schema: {type(exc).__name__}: {exc}",
            )
        if distilled.action == "none":
            return DistillationOutcome(
                action="none",
                provider=result.provider,
                model=result.model,
                duration_ms=result.duration_ms,
                usage=result.usage,
                raw_output=result.raw_output,
            )
        try:
            candidate = self._to_candidate(cluster, distilled, evidence, run_ids)
        except Exception as exc:
            return DistillationOutcome(
                action=distilled.action,
                provider=result.provider,
                model=result.model,
                duration_ms=result.duration_ms,
                usage=result.usage,
                raw_output=result.raw_output,
                error=f"invalid candidate: {type(exc).__name__}: {exc}",
            )
        return DistillationOutcome(
            candidate=candidate,
            action=distilled.action,
            provider=result.provider,
            model=result.model,
            duration_ms=result.duration_ms,
            usage=result.usage,
            raw_output=result.raw_output,
        )

    def _to_candidate(
        self,
        cluster: TaskPatternCluster,
        distilled: _Distilled,
        evidence: dict[str, str],
        run_ids: dict[str, tuple[str, ...]],
    ) -> SkillCandidate:
        source_run_ids: list[str] = []
        for task_id in cluster.task_ids:
            source_run_ids.extend(run_ids.get(task_id, ()))
        unique_runs: list[str] = []
        for run_id in source_run_ids:
            if run_id not in unique_runs:
                unique_runs.append(run_id)
        evidence_summary = _evidence_summary(evidence, cluster)
        action = SkillCandidateAction(distilled.action)
        proposed_name = distilled.proposed_name or ""
        if action is SkillCandidateAction.UPDATE and not proposed_name:
            proposed_name = distilled.existing_skill_name or ""
        return SkillCandidate(
            id=uuid4().hex,
            action=action,
            proposed_name=proposed_name,
            description=distilled.description or "",
            reason=distilled.reason or "",
            procedure=distilled.procedure,
            pitfalls=distilled.pitfalls,
            verification=distilled.verification,
            source_task_ids=tuple(cluster.task_ids),
            source_run_ids=tuple(unique_runs),
            existing_skill_name=distilled.existing_skill_name,
            status=SkillCandidateStatus.PENDING,
            created_at=datetime.now(UTC),
            evidence_summary=evidence_summary,
        )


def _evidence_summary(
    evidence: dict[str, str],
    cluster: TaskPatternCluster,
) -> str:
    """保存轻量 evidence 摘要（引用 ID + 关键结论），不复制完整 Trace。"""

    lines = [
        f"cluster={cluster.pattern_name}",
        f"tasks={','.join(cluster.task_ids)}",
        f"similarity={cluster.similarity_reason}",
    ]
    for task_id in cluster.task_ids:
        text = evidence.get(task_id, "")
        if not text:
            continue
        first = " ".join(text.split())[:240]
        lines.append(f"evidence[{task_id}] {first}")
    return "\n".join(lines)


__all__ = ["DistillationOutcome", "ProcedureDistiller"]
