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
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.registry import ModelAdapterRegistry
from app.models.types import ModelUsage
from app.skills import Skill, SkillMetadata

from ._call import ModelCallResult, call_model, parse_strict_json
from .config import SkillLearningSettings
from .models import (
    SkillCandidate,
    SkillCandidateAction,
    SkillCandidateStatus,
    TaskPatternCluster,
)
from .prompts import _DISTILLATION_PROMPT, _RELEVANCE_PROMPT

# Progressive Disclosure：最多加载 1~3 个"可能相关"的 Existing Skill 完整正文，
# 不把全部 Skill 全文塞给 Distiller。
_MAX_RELATED_SKILLS = 3
_MAX_SKILL_BODY_CHARS = 4000


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
    """一次蒸馏的结果；candidate 为空表示 action=none 或失败。

    ``reason`` / ``proposed_name`` / ``existing_skill_name`` 是模型的实际判断，
    即使 action=none 也会保留，用于 Live Eval 报告解释"为什么不沉淀"。
    ``related_skill_names`` 记录本次按需加载的 Existing Skill 正文（≤3）；
    ``model_call_count`` 记录本次蒸馏实际发生的模型调用数（相关性筛选 + 最终判断）。
    """

    model_config = ConfigDict(extra="forbid")

    candidate: SkillCandidate | None = None
    action: str | None = None
    reason: str | None = None
    proposed_name: str | None = None
    existing_skill_name: str | None = None
    related_skill_names: tuple[str, ...] = ()
    model_call_count: int = 1
    provider: str | None = None
    model: str | None = None
    duration_ms: float = 0.0
    usage: ModelUsage = Field(default_factory=ModelUsage)
    raw_output: str | None = None
    error: str | None = None


class _RelevanceOutcome(BaseModel):
    """相关性筛选的轻量结果。"""

    model_config = ConfigDict(extra="forbid")

    selected: tuple[str, ...] = ()
    usage: ModelUsage = Field(default_factory=ModelUsage)
    duration_ms: float = 0.0
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
        skill_loader: Callable[[str], Awaitable[Skill | None]] | None = None,
    ) -> DistillationOutcome:
        """对单个 Cluster 做蒸馏；action=none 返回空 candidate。

        Progressive Disclosure：先只用 catalog 的 name + description 筛选出
        可能相关的 Skill（≤3），再用 ``skill_loader`` 加载它们的完整正文，
        最后让模型基于正文判断 CREATE / UPDATE / NONE。catalog 为空或未提供
        loader 时跳过筛选，直接最终判断（无现有 Skill → CREATE 方向）。

        ``pending_candidates`` 用于判断新 pattern 是否已被一个待评审 Candidate
        覆盖，避免重复创建同义 Candidate。
        """

        related_names: tuple[str, ...] = ()
        related_bodies: dict[str, str] = {}
        extra_usage = ModelUsage()
        model_call_count = 1
        relevance_duration = 0.0
        if catalog and skill_loader is not None:
            relevance = await self._select_related_skills(cluster, catalog)
            if relevance.error is None and relevance.selected:
                selected = set(relevance.selected)
                names = tuple(
                    item.name
                    for item in catalog
                    if item.name in selected
                )[:_MAX_RELATED_SKILLS]
                bodies: dict[str, str] = {}
                for name in names:
                    skill = await skill_loader(name)
                    if skill is not None:
                        bodies[name] = _clip_skill_body(skill.content)
                related_names = names
                related_bodies = bodies
            extra_usage = _merge_usage(extra_usage, relevance.usage)
            if relevance.usage.total_tokens:
                model_call_count += 1
                relevance_duration = relevance.duration_ms

        user_payload: dict[str, Any] = {
            "cluster": cluster.model_dump(mode="json"),
            "evidence": evidence,
            "catalog": [
                {"name": item.name, "description": item.description}
                for item in catalog
            ],
            "related_skills": [
                {"name": name, "body": body}
                for name, body in related_bodies.items()
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
        total_usage = _merge_usage(extra_usage, result.usage)
        total_duration = relevance_duration + result.duration_ms
        if not result.ok:
            return DistillationOutcome(
                provider=result.provider,
                model=result.model,
                duration_ms=total_duration,
                usage=total_usage,
                raw_output=result.raw_output,
                related_skill_names=related_names,
                model_call_count=model_call_count,
                error=result.error,
            )
        payload = parse_strict_json(result.raw_output or "")
        if payload is None:
            return DistillationOutcome(
                provider=result.provider,
                model=result.model,
                duration_ms=total_duration,
                usage=total_usage,
                raw_output=result.raw_output,
                related_skill_names=related_names,
                model_call_count=model_call_count,
                error="distillation returned non-JSON output",
            )
        try:
            distilled = _Distilled.model_validate(payload)
        except Exception as exc:
            return DistillationOutcome(
                provider=result.provider,
                model=result.model,
                duration_ms=total_duration,
                usage=total_usage,
                raw_output=result.raw_output,
                related_skill_names=related_names,
                model_call_count=model_call_count,
                error=f"invalid distillation schema: {type(exc).__name__}: {exc}",
            )
        if distilled.action == "none":
            return DistillationOutcome(
                action="none",
                provider=result.provider,
                model=result.model,
                duration_ms=total_duration,
                usage=total_usage,
                raw_output=result.raw_output,
                reason=distilled.reason,
                proposed_name=distilled.proposed_name,
                existing_skill_name=distilled.existing_skill_name,
                related_skill_names=related_names,
                model_call_count=model_call_count,
            )
        try:
            candidate = self._to_candidate(
                cluster, distilled, evidence, run_ids, catalog
            )
        except Exception as exc:
            return DistillationOutcome(
                action=distilled.action,
                provider=result.provider,
                model=result.model,
                duration_ms=total_duration,
                usage=total_usage,
                raw_output=result.raw_output,
                reason=distilled.reason,
                proposed_name=distilled.proposed_name,
                existing_skill_name=distilled.existing_skill_name,
                related_skill_names=related_names,
                model_call_count=model_call_count,
                error=f"invalid candidate: {type(exc).__name__}: {exc}",
            )
        return DistillationOutcome(
            candidate=candidate,
            action=distilled.action,
            reason=candidate.reason,
            proposed_name=candidate.proposed_name,
            existing_skill_name=candidate.existing_skill_name,
            provider=result.provider,
            model=result.model,
            duration_ms=total_duration,
            usage=total_usage,
            raw_output=result.raw_output,
            related_skill_names=related_names,
            model_call_count=model_call_count,
        )

    async def _select_related_skills(
        self,
        cluster: TaskPatternCluster,
        catalog: Sequence[SkillMetadata],
    ) -> _RelevanceOutcome:
        """用 name + description 轻量筛选可能相关的 Skill（≤3，可空）。"""

        user_payload: dict[str, Any] = {
            "cluster": {
                "pattern_name": cluster.pattern_name,
                "description": cluster.description,
                "similarity_reason": cluster.similarity_reason,
            },
            "catalog": [
                {"name": item.name, "description": item.description}
                for item in catalog
            ],
        }
        user_content = json.dumps(
            user_payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        result: ModelCallResult = await call_model(
            self._registry,
            system_prompt=_RELEVANCE_PROMPT,
            user_content=user_content,
            settings=self.settings,
            default_provider=self._default_provider,
            default_model=self._default_model,
        )
        if not result.ok:
            return _RelevanceOutcome(
                usage=result.usage,
                duration_ms=result.duration_ms,
                error=result.error,
            )
        payload = parse_strict_json(result.raw_output or "")
        if payload is None or not isinstance(payload.get("related_skills"), list):
            return _RelevanceOutcome(
                usage=result.usage,
                duration_ms=result.duration_ms,
                error="relevance returned non-JSON output",
            )
        selected: list[str] = []
        for item in payload["related_skills"]:
            if isinstance(item, str) and item.strip():
                name = item.strip()
                if name not in selected:
                    selected.append(name)
        return _RelevanceOutcome(
            selected=tuple(selected),
            usage=result.usage,
            duration_ms=result.duration_ms,
        )

    def _to_candidate(
        self,
        cluster: TaskPatternCluster,
        distilled: _Distilled,
        evidence: dict[str, str],
        run_ids: dict[str, tuple[str, ...]],
        catalog: Sequence[SkillMetadata] = (),
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
        description = distilled.description or ""
        if action is SkillCandidateAction.UPDATE:
            # UPDATE：模型提供了 description 就用模型输出；否则继承
            # existing_skill_name 对应 Skill 的 description。existing_skill_name
            # 在 catalog 中找不到 → 明确报错，不静默生成空/兜底 description。
            if not description:
                description = _catalog_description(
                    catalog, distilled.existing_skill_name
                )
                if not description:
                    raise ValueError(
                        "update candidate requires a non-empty description: model "
                        f"did not provide one and existing_skill_name "
                        f"{distilled.existing_skill_name!r} was not found in the "
                        "skill catalog"
                    )
        else:
            # CREATE：模型必须提供 description，不允许继承。
            if not description:
                raise ValueError(
                    "create candidate requires a non-empty description"
                )
        return SkillCandidate(
            id=uuid4().hex,
            action=action,
            proposed_name=proposed_name,
            description=description,
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


def _clip_skill_body(content: str) -> str:
    """折叠空白并截断 Skill 正文，避免单条正文过长。"""

    text = " ".join(content.split())
    if len(text) <= _MAX_SKILL_BODY_CHARS:
        return text
    return text[:_MAX_SKILL_BODY_CHARS] + "…[截断]"


def _catalog_description(
    catalog: Sequence[SkillMetadata],
    name: str | None,
) -> str:
    """从 catalog 查同名 Skill 的 description；找不到返回空串。"""

    if not name:
        return ""
    for item in catalog:
        if item.name == name:
            return item.description
    return ""


def _merge_usage(total: ModelUsage, current: ModelUsage) -> ModelUsage:
    """聚合两次模型调用的 token 用量（保留 total 的扩展字段）。"""

    return total.model_copy(
        update={
            "input_tokens": total.input_tokens + current.input_tokens,
            "output_tokens": total.output_tokens + current.output_tokens,
            "total_tokens": total.total_tokens + current.total_tokens,
        }
    )


__all__ = ["DistillationOutcome", "ProcedureDistiller"]
