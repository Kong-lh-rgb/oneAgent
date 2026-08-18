"""Skill Learning Service：编排 Completed Task → Candidate 的完整流水线。

单向依赖：Runtime 只负责产生 Task 与 Trace 证据；本 Service 消费它们。
- ``maybe_run_mining()`` 由 CLI 在每次交互后调用，内部通过 watermark 决定是否触发；
- 只有达到 batch_size 才调用 Pattern Mining；只有发现 Cluster 才进入 Distillation；
- Candidate 创建后不自动写正式 Skill，必须经过 Human Gate（accept / reject）。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.models.registry import ModelAdapterRegistry
from app.models.types import ModelUsage
from app.skills import Skill, SkillScope, SkillStore
from app.task import FileTaskStore, Task, TaskStatus
from app.trace.store import SQLiteTraceStore

from .config import SkillLearningSettings
from .distiller import DistillationOutcome, ProcedureDistiller
from .evidence import TraceEvidenceBuilder
from .miner import PatternMiningOutcome, TaskPatternMiner
from .models import (
    SkillCandidate,
    SkillCandidateAction,
    SkillCandidateStatus,
    TaskCard,
    TaskPatternCluster,
)
from .store import InflightBatch, SkillCandidateStore
from .trace_selector import TaskTraceSelector

logger = logging.getLogger("oneagent.skill_learning.service")

_MAX_COMPLETED_TASKS = 1_000_000


class DistillationRecord(BaseModel):
    """一次蒸馏的报告记录（action=none 也保留模型实际判断与理由）。"""

    model_config = ConfigDict(extra="forbid")

    cluster_name: str
    action: str | None = None
    reason: str | None = None
    proposed_name: str | None = None
    existing_skill_name: str | None = None
    related_skill_names: tuple[str, ...] = ()
    error: str | None = None


class SkillLearningOutcome(BaseModel):
    """一次 maybe_run_mining 的结构化结果（含完整 Usage 聚合）。"""

    model_config = ConfigDict(extra="forbid")

    triggered: bool = False
    skipped_reason: str | None = None
    pending_count: int = 0
    scanned_task_count: int = 0
    cluster_count: int = 0
    clusters: tuple[TaskPatternCluster, ...] = ()
    candidate_count: int = 0
    distillations: tuple[DistillationRecord, ...] = ()
    usage: ModelUsage = Field(default_factory=ModelUsage)
    pattern_mining_calls: int = 0
    distillation_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    pattern_mining_duration_ms: float = 0.0
    distillation_duration_ms: float = 0.0
    total_duration_ms: float = 0.0
    error: str | None = None


class SkillLearningService:
    """从 Completed Task 提炼 Skill Candidate 的独立服务。"""

    def __init__(
        self,
        task_store: FileTaskStore,
        trace_store: SQLiteTraceStore,
        skill_store: SkillStore,
        candidate_store: SkillCandidateStore,
        registry: ModelAdapterRegistry,
        *,
        settings: SkillLearningSettings | None = None,
        default_provider: str | None = None,
        default_model: str | None = None,
    ) -> None:
        self.task_store = task_store
        self.trace_store = trace_store
        self.skill_store = skill_store
        self.candidate_store = candidate_store
        self._registry = registry
        self.settings = settings or SkillLearningSettings()
        self._default_provider = default_provider
        self._default_model = default_model
        self.miner = TaskPatternMiner(
            registry,
            settings=self.settings,
            default_provider=default_provider,
            default_model=default_model,
        )
        self.distiller = ProcedureDistiller(
            registry,
            settings=self.settings,
            default_provider=default_provider,
            default_model=default_model,
        )
        self.evidence_builder = TraceEvidenceBuilder(self.settings)
        self.trace_selector = TaskTraceSelector()

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    async def maybe_run_mining(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> SkillLearningOutcome:
        """按 watermark 决定是否触发一次 Pattern Mining。

        每累计 batch_size 个新 Completed Task 才触发一次；模型失败时保留
        inflight batch 供下次触发点重试（at-least-once），成功后移入
        processed 永不重复。一次调用最多尝试一次模型调用。
        """

        if not self.settings.skill_learning_enabled:
            return SkillLearningOutcome(skipped_reason="disabled")
        completed = await self.task_store.list(
            status=TaskStatus.COMPLETED,
            limit=_MAX_COMPLETED_TASKS,
        )
        by_id = {task.id: task for task in completed}
        watermark = await self.candidate_store.load_watermark()
        processed = set(watermark.processed_task_ids)
        pending = list(watermark.pending_task_ids)
        inflight = watermark.inflight

        # 1) 无遗留 inflight 时，累积新 Completed Task 到 pending。
        if inflight is None:
            known = processed | set(pending)
            new_ids = [task.id for task in completed if task.id not in known]
            for task_id in new_ids:
                if task_id not in pending:
                    pending.append(task_id)
            await self.candidate_store.save_watermark(
                watermark.model_copy(update={"pending_task_ids": tuple(pending)})
            )
            if len(pending) < self.settings.skill_learning_batch_size:
                return SkillLearningOutcome(
                    pending_count=len(pending),
                    skipped_reason="batch_not_ready",
                )
            scan_ids = tuple(
                pending[: self.settings.skill_learning_max_tasks_per_scan]
            )
            scan_set = set(scan_ids)
            new_pending = tuple(
                task_id for task_id in pending if task_id not in scan_set
            )
            inflight = InflightBatch(
                batch_id=uuid4().hex,
                task_ids=scan_ids,
                started_at=datetime.now(UTC),
            )
            await self.candidate_store.save_watermark(
                watermark.model_copy(
                    update={
                        "pending_task_ids": new_pending,
                        "inflight": inflight,
                        "last_error": None,
                    }
                )
            )
        else:
            scan_ids = inflight.task_ids
            new_pending = tuple(pending)

        cards = tuple(
            _to_card(by_id[task_id])
            for task_id in scan_ids
            if task_id in by_id
        )
        if not cards:
            # 任务文件可能已删除：无法读取，直接结束该 batch。
            new_processed = tuple(sorted(processed | set(scan_ids)))
            await self.candidate_store.save_watermark(
                watermark.model_copy(
                    update={
                        "processed_task_ids": new_processed,
                        "pending_task_ids": new_pending,
                        "inflight": None,
                        "last_mining_at": datetime.now(UTC),
                    }
                )
            )
            return SkillLearningOutcome(
                triggered=True,
                pending_count=len(new_pending),
                scanned_task_count=0,
                error="no readable completed tasks to scan",
            )

        # 2) Pattern Mining（一次模型调用）。
        mining: PatternMiningOutcome = await self.miner.mine(cards)
        usage = mining.usage
        pattern_duration = mining.duration_ms

        if mining.error:
            # 模型失败：不 processed，保留 inflight 供下次触发点重试。
            new_attempt = inflight.attempt + 1
            if new_attempt >= self.settings.skill_learning_max_attempts:
                # 已用尽尝试额度：放弃该 batch（标记 processed），避免无限重试。
                new_processed = tuple(sorted(processed | set(scan_ids)))
                await self.candidate_store.save_watermark(
                    watermark.model_copy(
                        update={
                            "processed_task_ids": new_processed,
                            "pending_task_ids": new_pending,
                            "inflight": None,
                            "last_error": mining.error,
                            "last_mining_at": datetime.now(UTC),
                        }
                    )
                )
                return SkillLearningOutcome(
                    triggered=True,
                    pending_count=len(new_pending),
                    scanned_task_count=len(cards),
                    usage=usage,
                    pattern_mining_calls=1,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    total_tokens=usage.total_tokens,
                    pattern_mining_duration_ms=pattern_duration,
                    total_duration_ms=pattern_duration,
                    error=(
                        "pattern mining failed after "
                        f"{self.settings.skill_learning_max_attempts} attempts: "
                        f"{mining.error}"
                    ),
                )
            await self.candidate_store.save_watermark(
                watermark.model_copy(
                    update={
                        "inflight": inflight.model_copy(
                            update={
                                "attempt": new_attempt,
                                "last_error": mining.error,
                            }
                        ),
                        "last_error": mining.error,
                    }
                )
            )
            logger.warning(
                "pattern mining failed (attempt %s/%s): %s",
                new_attempt,
                self.settings.skill_learning_max_attempts,
                mining.error,
            )
            return SkillLearningOutcome(
                triggered=True,
                pending_count=len(new_pending),
                scanned_task_count=len(cards),
                usage=usage,
                pattern_mining_calls=1,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                total_tokens=usage.total_tokens,
                pattern_mining_duration_ms=pattern_duration,
                total_duration_ms=pattern_duration,
                error=mining.error,
            )

        # 3) 成功（mining 无 error）。没有 cluster → 直接 processed。
        if not mining.clusters:
            new_processed = tuple(sorted(processed | set(scan_ids)))
            await self.candidate_store.save_watermark(
                watermark.model_copy(
                    update={
                        "processed_task_ids": new_processed,
                        "pending_task_ids": new_pending,
                        "inflight": None,
                        "last_error": None,
                        "last_mining_at": datetime.now(UTC),
                    }
                )
            )
            return SkillLearningOutcome(
                triggered=True,
                pending_count=len(new_pending),
                scanned_task_count=len(cards),
                cluster_count=0,
                clusters=(),
                usage=usage,
                pattern_mining_calls=1,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                total_tokens=usage.total_tokens,
                pattern_mining_duration_ms=pattern_duration,
                total_duration_ms=pattern_duration,
            )

        # 4) 有 clusters：先保持 batch inflight，完成全部 Cluster 的 Evidence →
        #    Distillation → Candidate 后再标记 processed。某个 Cluster 蒸馏失败时
        #    保留 inflight 供后续触发点重试（不提前 processed，已创建的 Candidate
        #    靠 duplicate-source / pending 去重，避免重试重复创建）。
        created: list[SkillCandidate] = []
        errors: list[str] = []
        distillations: list[DistillationRecord] = []
        distill_usage = ModelUsage()
        distill_duration = 0.0
        distill_calls = 0
        pending_candidates = await self.list_candidates(
            status=SkillCandidateStatus.PENDING
        )
        catalog = await self.skill_store.catalog()
        for cluster in mining.clusters:
            if await self.candidate_store.find_duplicate_source(cluster.task_ids):
                continue
            evidence_map: dict[str, str] = {}
            run_ids_map: dict[str, tuple[str, ...]] = {}
            for task_id in cluster.task_ids:
                task = by_id.get(task_id)
                if task is None:
                    continue
                run_ids_map[task_id] = task.run_ids
                events = await self._load_task_events(task)
                evidence_map[task_id] = self.evidence_builder.build(task, events)
            logger.debug(
                "cluster %s evidence chars: %s",
                cluster.pattern_name,
                {k: len(v) for k, v in evidence_map.items()},
            )
            distill: DistillationOutcome = await self.distiller.distill(
                cluster,
                evidence=evidence_map,
                run_ids=run_ids_map,
                catalog=catalog,
                pending_candidates=pending_candidates,
                skill_loader=self.skill_store.load,
            )
            distillations.append(
                DistillationRecord(
                    cluster_name=cluster.pattern_name,
                    action=distill.action,
                    reason=distill.reason,
                    proposed_name=distill.proposed_name,
                    existing_skill_name=distill.existing_skill_name,
                    related_skill_names=distill.related_skill_names,
                    error=distill.error,
                )
            )
            distill_calls += distill.model_call_count
            distill_usage = _add_usage(distill_usage, distill.usage)
            distill_duration += distill.duration_ms
            if distill.error:
                errors.append(f"{cluster.pattern_name}: {distill.error}")
                continue
            if distill.candidate is None:
                continue
            # 防线：pending 已有同名 Candidate → 不重复创建。
            if _pending_name_exists(
                pending_candidates,
                distill.candidate.proposed_name,
            ):
                continue
            await self.candidate_store.create(distill.candidate)
            created.append(distill.candidate)
            pending_candidates = pending_candidates + (distill.candidate,)

        total_usage = _add_usage(usage, distill_usage)
        total_duration = pattern_duration + distill_duration

        if errors:
            # 蒸馏部分失败：不 processed，保留 inflight 供下次触发点重试；
            # 达到 max_attempts 才放弃（标记 processed，防无限重试）。
            distill_error = "; ".join(errors)
            new_attempt = inflight.attempt + 1
            if new_attempt >= self.settings.skill_learning_max_attempts:
                new_processed = tuple(sorted(processed | set(scan_ids)))
                await self.candidate_store.save_watermark(
                    watermark.model_copy(
                        update={
                            "processed_task_ids": new_processed,
                            "pending_task_ids": new_pending,
                            "inflight": None,
                            "last_error": distill_error,
                            "last_mining_at": datetime.now(UTC),
                        }
                    )
                )
                return SkillLearningOutcome(
                    triggered=True,
                    pending_count=len(new_pending),
                    scanned_task_count=len(cards),
                    cluster_count=len(mining.clusters),
                    clusters=mining.clusters,
                    candidate_count=len(created),
                    distillations=tuple(distillations),
                    usage=total_usage,
                    pattern_mining_calls=1,
                    distillation_calls=distill_calls,
                    input_tokens=total_usage.input_tokens,
                    output_tokens=total_usage.output_tokens,
                    total_tokens=total_usage.total_tokens,
                    pattern_mining_duration_ms=pattern_duration,
                    distillation_duration_ms=distill_duration,
                    total_duration_ms=total_duration,
                    error=(
                        "distillation failed after "
                        f"{self.settings.skill_learning_max_attempts} attempts: "
                        f"{distill_error}"
                    ),
                )
            await self.candidate_store.save_watermark(
                watermark.model_copy(
                    update={
                        "inflight": inflight.model_copy(
                            update={
                                "attempt": new_attempt,
                                "last_error": distill_error,
                            }
                        ),
                        "last_error": distill_error,
                    }
                )
            )
            logger.warning(
                "distillation failed (attempt %s/%s): %s",
                new_attempt,
                self.settings.skill_learning_max_attempts,
                distill_error,
            )
            return SkillLearningOutcome(
                triggered=True,
                pending_count=len(new_pending),
                scanned_task_count=len(cards),
                cluster_count=len(mining.clusters),
                clusters=mining.clusters,
                candidate_count=len(created),
                distillations=tuple(distillations),
                usage=total_usage,
                pattern_mining_calls=1,
                distillation_calls=distill_calls,
                input_tokens=total_usage.input_tokens,
                output_tokens=total_usage.output_tokens,
                total_tokens=total_usage.total_tokens,
                pattern_mining_duration_ms=pattern_duration,
                distillation_duration_ms=distill_duration,
                total_duration_ms=total_duration,
                error=distill_error,
            )

        # 全部 Cluster 处理成功（含 action=none）→ 标记 processed。
        new_processed = tuple(sorted(processed | set(scan_ids)))
        await self.candidate_store.save_watermark(
            watermark.model_copy(
                update={
                    "processed_task_ids": new_processed,
                    "pending_task_ids": new_pending,
                    "inflight": None,
                    "last_error": None,
                    "last_mining_at": datetime.now(UTC),
                }
            )
        )
        return SkillLearningOutcome(
            triggered=True,
            pending_count=len(new_pending),
            scanned_task_count=len(cards),
            cluster_count=len(mining.clusters),
            clusters=mining.clusters,
            candidate_count=len(created),
            distillations=tuple(distillations),
            usage=total_usage,
            pattern_mining_calls=1,
            distillation_calls=distill_calls,
            input_tokens=total_usage.input_tokens,
            output_tokens=total_usage.output_tokens,
            total_tokens=total_usage.total_tokens,
            pattern_mining_duration_ms=pattern_duration,
            distillation_duration_ms=distill_duration,
            total_duration_ms=total_duration,
            error="; ".join(errors) or None,
        )

    async def _load_task_events(self, task: Task) -> tuple:
        """读取 Task.run_ids 关联的 Trace，用 task_update 锚点筛选相关事件。

        只返回"完成当前 Task 的实际执行过程"（Anchor 之间的 Agent Step 区间），
        不是整个 Run，也不是只有 task_update 本身。无有效 Anchor 时返回空，
        由 Evidence Builder 走 Task-only fallback。
        """

        run_events: dict[str, tuple] = {}
        for run_id in task.run_ids:
            try:
                run_events[run_id] = await self.trace_store.load_events(run_id)
            except (KeyError, ValueError, OSError):
                continue
        if not run_events:
            return ()
        return self.trace_selector.select(
            task,
            run_events,
            max_events=self.settings.skill_learning_max_events_per_task,
        )

    # ------------------------------------------------------------------
    # Candidate 查询
    # ------------------------------------------------------------------

    async def list_candidates(
        self,
        *,
        status: SkillCandidateStatus | None = None,
    ) -> tuple[SkillCandidate, ...]:
        return await self.candidate_store.list(status=status)

    async def get_candidate(self, candidate_id: str) -> SkillCandidate | None:
        return await self.candidate_store.get(candidate_id)

    # ------------------------------------------------------------------
    # Human Gate
    # ------------------------------------------------------------------

    async def accept(
        self,
        candidate_id: str,
        *,
        scope: str | None = None,
    ) -> tuple[SkillCandidate, Path | None]:
        """接受候选（Human Gate 是最终决策点）。

        - CREATE：创建正式 <scope>/<name>/SKILL.md，成功后 ACCEPTED；
        - UPDATE：原子覆盖 existing_skill_name 对应的正式 SKILL.md，成功后 ACCEPTED；
          不再生成"尚未应用的 Proposal"。
        写入失败时抛明确错误，正式 Skill 与 Candidate 均保持原样（不会出现
        "文件未更新但 Candidate 已 ACCEPTED"）。
        """

        candidate = await self.candidate_store.get(candidate_id)
        if candidate is None:
            raise KeyError(f"candidate not found: {candidate_id}")
        if candidate.status is not SkillCandidateStatus.PENDING:
            raise ValueError(f"candidate is not pending: {candidate.status.value}")
        resolved_scope = _resolve_scope(
            scope or self.settings.skill_learning_default_scope
        )
        target: Path | None = None
        if candidate.action is SkillCandidateAction.CREATE:
            target = await self._create_skill(candidate, resolved_scope)
        else:
            target = await self._update_skill(candidate)
        updated = candidate.model_copy(
            update={
                "status": SkillCandidateStatus.ACCEPTED,
                "reviewed_at": datetime.now(UTC),
            }
        )
        await self.candidate_store.update(updated)
        return updated, target

    async def reject(self, candidate_id: str) -> SkillCandidate:
        """拒绝候选（不产生正式 Skill）。"""

        candidate = await self.candidate_store.get(candidate_id)
        if candidate is None:
            raise KeyError(f"candidate not found: {candidate_id}")
        if candidate.status is not SkillCandidateStatus.PENDING:
            raise ValueError(f"candidate is not pending: {candidate.status.value}")
        updated = candidate.model_copy(
            update={
                "status": SkillCandidateStatus.REJECTED,
                "reviewed_at": datetime.now(UTC),
            }
        )
        await self.candidate_store.update(updated)
        return updated

    async def _create_skill(
        self,
        candidate: SkillCandidate,
        scope: SkillScope,
    ) -> Path:
        skill_dir = (
            self.skill_store.project_dir
            if scope is SkillScope.PROJECT
            else self.skill_store.user_dir
        )
        existing = await self.skill_store.load(candidate.proposed_name)
        if existing is not None:
            raise ValueError(
                f"skill '{candidate.proposed_name}' already exists; "
                "use an update candidate instead"
            )
        target = skill_dir / candidate.proposed_name / "SKILL.md"
        if target.exists() or target.is_symlink():
            raise ValueError(f"skill file already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_render_new_skill(candidate), encoding="utf-8")
        return target

    async def _update_skill(self, candidate: SkillCandidate) -> Path:
        """接受 UPDATE：原子覆盖 existing_skill_name 对应的正式 SKILL.md。

        安全性：
        - existing_skill_name 非空且能 load 到正式 Skill；
        - 目标路径来自这个 Existing Skill 的真实 SKILL.md（不使用 proposed_name
          改路径 / 不修改其他 Skill）；
        - 用 Candidate 的 description/procedure/pitfalls/verification 渲染完整
          SKILL.md（不追加 Proposal / 审计信息）；
        - 临时文件 → flush → replace 原子写；写失败抛错，正式 Skill 与 Candidate
          均保持原样。
        """

        name = candidate.existing_skill_name
        if not name:
            raise ValueError("update candidate requires existing_skill_name")
        existing = await self.skill_store.load(name)
        if existing is None:
            raise ValueError(f"existing skill '{name}' not found; cannot update")
        # 目标必须是该 Existing Skill 的真实 SKILL.md 路径。
        target = existing.metadata.location
        if target.name != "SKILL.md" or target.parent.name != existing.metadata.name:
            raise ValueError(
                f"refusing to update unexpected skill path: {target}"
            )
        markdown = _render_updated_skill(candidate, existing)
        _atomic_write_text(target, markdown)
        return target

    # ------------------------------------------------------------------
    # CLI 展示
    # ------------------------------------------------------------------

    def render_candidate_details(self, candidate: SkillCandidate) -> str:
        """渲染给终端的人工评审详情。"""

        lines = [
            f"Proposed Skill: {candidate.proposed_name}",
            f"Action: {candidate.action.value.upper()}",
            f"Status: {candidate.status.value}",
            "",
            f"Why: {candidate.reason}",
            "",
            f"Source Tasks: {' '.join(candidate.source_task_ids)}",
            f"Source Runs: {' '.join(candidate.source_run_ids) or '（无）'}",
            "",
            "Common Procedure:",
        ]
        for index, step in enumerate(candidate.procedure, 1):
            lines.append(f"{index}. {step}")
        if candidate.pitfalls:
            lines.append("")
            lines.append("Repeated Problems:")
            lines.extend(f"- {item}" for item in candidate.pitfalls)
        if candidate.verification:
            lines.append("")
            lines.append("Verification:")
            lines.extend(f"- {item}" for item in candidate.verification)
        if candidate.evidence_summary:
            lines.append("")
            lines.append("Evidence Summary:")
            lines.append(candidate.evidence_summary)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _add_usage(total: ModelUsage, current: ModelUsage) -> ModelUsage:
    """聚合两次模型调用的 token 用量（保留 total 的扩展字段）。"""

    return total.model_copy(
        update={
            "input_tokens": total.input_tokens + current.input_tokens,
            "output_tokens": total.output_tokens + current.output_tokens,
            "total_tokens": total.total_tokens + current.total_tokens,
        }
    )


def _pending_name_exists(
    candidates: Sequence[SkillCandidate],
    proposed_name: str,
) -> bool:
    """Service 层确定性 exact-name 防重：pending 已有同名 Candidate 则拒绝。"""

    if not proposed_name:
        return False
    return any(
        candidate.proposed_name == proposed_name for candidate in candidates
    )


def _to_card(task: Task) -> TaskCard:
    return TaskCard(
        task_id=task.id,
        title=task.title,
        description=task.description,
        goal=task.goal,
        constraints=task.constraints,
        key_facts=task.key_facts,
        final_steps=tuple(
            step.title for step in task.steps if step.status.value == "done"
        ),
        created_at=task.created_at,
        completed_at=task.completed_at,
        run_count=len(task.run_ids),
    )


def _resolve_scope(value: str) -> SkillScope:
    normalized = value.strip().lower()
    if normalized in ("project", "project_scope", SkillScope.PROJECT.value):
        return SkillScope.PROJECT
    if normalized in ("user", "user_scope", SkillScope.USER.value):
        return SkillScope.USER
    raise ValueError(f"invalid skill scope: {value}")


def _render_new_skill(candidate: SkillCandidate) -> str:
    title = candidate.proposed_name.replace("-", " ").title()
    lines = [
        "---",
        f"name: {candidate.proposed_name}",
        f"description: {candidate.description}",
        "---",
        "",
        f"# {title}",
        "",
    ]
    lines.append("## Procedure")
    lines.append("")
    for index, step in enumerate(candidate.procedure, 1):
        lines.append(f"{index}. {step}")
    if candidate.pitfalls:
        lines.append("")
        lines.append("## Pitfalls")
        lines.append("")
        lines.extend(f"- {item}" for item in candidate.pitfalls)
    if candidate.verification:
        lines.append("")
        lines.append("## Verification")
        lines.append("")
        lines.extend(f"- {item}" for item in candidate.verification)
    return "\n".join(lines) + "\n"


def _render_updated_skill(candidate: SkillCandidate, existing: Skill) -> str:
    """渲染 UPDATE 后的完整正式 SKILL.md（干净，无提案/审计信息）。

    - name 固定为 existing_skill_name（UPDATE 的目标唯一由它决定，不因
      proposed_name 异常而重命名）；
    - description 使用 candidate.description（模型新描述或继承的旧描述）；
    - procedure/pitfalls/verification 全部来自 Candidate（V1 不二次 merge）。
    审计信息（source task / reason / candidate id / review）保留在 Candidate Store，
    不进入正式 Skill。
    """

    name = candidate.existing_skill_name or existing.metadata.name
    title = name.replace("-", " ").title()
    lines = [
        "---",
        f"name: {name}",
        f"description: {candidate.description}",
        "---",
        "",
        f"# {title}",
        "",
        "## Procedure",
        "",
    ]
    for index, step in enumerate(candidate.procedure, 1):
        lines.append(f"{index}. {step}")
    if candidate.pitfalls:
        lines.append("")
        lines.append("## Pitfalls")
        lines.append("")
        lines.extend(f"- {item}" for item in candidate.pitfalls)
    if candidate.verification:
        lines.append("")
        lines.append("## Verification")
        lines.append("")
        lines.extend(f"- {item}" for item in candidate.verification)
    return "\n".join(lines) + "\n"


def _atomic_write_text(path: Path, content: str) -> None:
    """原子写文本文件：临时文件 → flush → replace。

    避免写一半导致正式 Skill 损坏；失败时原文件保持原样。
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(
        f".{path.name}.tmp.{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}"
    )
    with temp.open("w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
    temp.replace(path)


__all__ = ["SkillLearningOutcome", "SkillLearningService"]
