"""Skill Learning Service：编排 Completed Task → Candidate 的完整流水线。

单向依赖：Runtime 只负责产生 Task 与 Trace 证据；本 Service 消费它们。
- ``maybe_run_mining()`` 由 CLI 在每次交互后调用，内部通过 watermark 决定是否触发；
- 只有达到 batch_size 才调用 Pattern Mining；只有发现 Cluster 才进入 Distillation；
- Candidate 创建后不自动写正式 Skill，必须经过 Human Gate（accept / reject）。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

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
)
from .store import SkillCandidateStore

logger = logging.getLogger("oneagent.skill_learning.service")

_MAX_COMPLETED_TASKS = 1_000_000


class SkillLearningOutcome(BaseModel):
    """一次 maybe_run_mining 的结构化结果。"""

    model_config = ConfigDict(extra="forbid")

    triggered: bool = False
    skipped_reason: str | None = None
    pending_count: int = 0
    scanned_task_count: int = 0
    cluster_count: int = 0
    candidate_count: int = 0
    usage: ModelUsage = Field(default_factory=ModelUsage)
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

        每累计 batch_size 个新 Completed Task 才触发一次；已处理的 Task
        永不重复计数（watermark 持久化，重启后仍有效）。
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

        # 1) 新完成的 Task 进入 pending（去重）。
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

        # 2) 触发扫描：先推进 watermark，防止重复扫描 / 崩溃重扫。
        scan_ids = tuple(pending[: self.settings.skill_learning_max_tasks_per_scan])
        scan_set = set(scan_ids)
        new_processed = tuple(sorted(processed | scan_set))
        new_pending = tuple(task_id for task_id in pending if task_id not in scan_set)
        await self.candidate_store.save_watermark(
            watermark.model_copy(
                update={
                    "processed_task_ids": new_processed,
                    "pending_task_ids": new_pending,
                    "last_mining_at": datetime.now(UTC),
                }
            )
        )

        cards = tuple(
            _to_card(by_id[task_id])
            for task_id in scan_ids
            if task_id in by_id
        )
        if not cards:
            return SkillLearningOutcome(
                triggered=True,
                pending_count=len(new_pending),
                error="no readable completed tasks to scan",
            )

        # 3) Pattern Mining（只消费 TaskCard）。
        mining: PatternMiningOutcome = await self.miner.mine(cards)
        if mining.error:
            logger.warning("pattern mining failed: %s", mining.error)
            return SkillLearningOutcome(
                triggered=True,
                pending_count=len(new_pending),
                scanned_task_count=len(cards),
                usage=mining.usage,
                error=mining.error,
            )
        if not mining.clusters:
            return SkillLearningOutcome(
                triggered=True,
                pending_count=len(new_pending),
                scanned_task_count=len(cards),
                cluster_count=0,
                usage=mining.usage,
            )

        # 4) 每个 Cluster：Evidence → Distillation → Candidate（pending）。
        created: list[SkillCandidate] = []
        errors: list[str] = []
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
            distill: DistillationOutcome = await self.distiller.distill(
                cluster,
                evidence=evidence_map,
                run_ids=run_ids_map,
                catalog=await self.skill_store.catalog(),
            )
            if distill.error:
                errors.append(f"{cluster.pattern_name}: {distill.error}")
                continue
            if distill.candidate is None:
                continue
            await self.candidate_store.create(distill.candidate)
            created.append(distill.candidate)

        return SkillLearningOutcome(
            triggered=True,
            pending_count=len(new_pending),
            scanned_task_count=len(cards),
            cluster_count=len(mining.clusters),
            candidate_count=len(created),
            usage=mining.usage,
            error="; ".join(errors) or None,
        )

    async def _load_task_events(self, task: Task) -> tuple:
        """读取 Task.run_ids 关联的 Trace 事件；缺失 / 异常优雅降级为空。"""

        events: list = []
        for run_id in task.run_ids:
            try:
                loaded = await self.trace_store.load_events(run_id)
            except (KeyError, ValueError, OSError):
                continue
            events.extend(loaded)
            if len(events) >= self.settings.skill_learning_max_events_per_task:
                break
        return tuple(events)

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
        """接受候选：CREATE 生成正式 SKILL.md；UPDATE 生成 replacement proposal。

        不会静默覆盖已有 Skill。返回 (更新后的候选, 写入的路径或 None)。
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
            existing = await self.skill_store.load(candidate.existing_skill_name or "")
            markdown = _render_updated_skill(candidate, existing)
            target = await self.candidate_store.write_proposal(candidate.id, markdown)
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


def _render_updated_skill(
    candidate: SkillCandidate,
    existing: Skill | None,
) -> str:
    """生成 UPDATE 的 replacement SKILL.md（不直接覆盖正式文件）。"""

    if existing is not None:
        base = [
            "---",
            f"name: {existing.metadata.name}",
            f"description: {existing.metadata.description}",
            "---",
            "",
            existing.content.strip(),
        ]
        header = "\n".join(base)
    else:
        header = f"# {candidate.proposed_name.replace('-', ' ').title()}"
    lines = [
        header,
        "",
        "> 以下为 Skill Learning 生成的 UPDATE 提案，尚未应用到正式 Skill。",
        "",
        "## Procedure（提案）",
        "",
    ]
    for index, step in enumerate(candidate.procedure, 1):
        lines.append(f"{index}. {step}")
    if candidate.pitfalls:
        lines.append("")
        lines.append("## Pitfalls（提案）")
        lines.append("")
        lines.extend(f"- {item}" for item in candidate.pitfalls)
    if candidate.verification:
        lines.append("")
        lines.append("## Verification（提案）")
        lines.append("")
        lines.extend(f"- {item}" for item in candidate.verification)
    lines.append("")
    lines.append(
        f"来源 Task: {' '.join(candidate.source_task_ids)} · "
        f"原因: {candidate.reason}"
    )
    return "\n".join(lines) + "\n"


__all__ = ["SkillLearningOutcome", "SkillLearningService"]
