"""Skill Learning Eval 的运行器。

Learning 场景不是普通 Agent Run，而是：
1. 预置一批 Completed Task + Trace 事件（可选已有 Skill）；
2. 用 Mock/真实模型驱动 SkillLearningService.maybe_run_mining；
3. 可选执行 Human Gate（accept / reject）；
4. 采集 mining outcome、candidate 列表与最终可 discover 的 Skill。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.agent.events import AgentEvent, AgentEventType
from app.models.registry import ModelAdapterRegistry
from app.models.types import ToolCall, ToolResult
from app.skill_learning import (
    SkillCandidate,
    SkillCandidateAction,
    SkillCandidateStatus,
    SkillCandidateStore,
    SkillLearningService,
    SkillLearningSettings,
)
from app.skills import SkillStore
from app.task import FileTaskStore, TaskStatus, TaskStep
from app.trace.store import SQLiteTraceStore

from .scenario import Scenario


@dataclass
class LearningEvalEnvironment:
    """Learning 场景的临时环境与预置状态。"""

    root: Path
    task_store: FileTaskStore
    trace_store: SQLiteTraceStore
    skill_store: SkillStore
    candidate_store: SkillCandidateStore
    task_ids: tuple[str, ...] = ()
    task_aliases: dict[str, str] = field(default_factory=dict)


@dataclass
class LearningEvalOutcome:
    """一次 Learning 场景运行的全部真实结果。"""

    scenario: Scenario
    environment: LearningEvalEnvironment
    mining: object | None = None
    candidates: tuple[SkillCandidate, ...] = ()
    created_skills: tuple[str, ...] = ()
    error: str | None = None


def _trace_event(run_id: str, sequence: int, spec) -> AgentEvent:
    """把 InitialTraceEvent 转为 AgentEvent。"""

    tool_call = (
        ToolCall(id=f"c{sequence}", name=spec.tool_name, arguments=spec.arguments)
        if spec.tool_name
        else None
    )
    if spec.type == "tool_started":
        return AgentEvent(
            run_id=run_id,
            conversation_id="learning",
            sequence=sequence,
            type=AgentEventType.TOOL_STARTED,
            tool_call=tool_call,
        )
    if spec.type == "tool_completed":
        return AgentEvent(
            run_id=run_id,
            conversation_id="learning",
            sequence=sequence,
            type=AgentEventType.TOOL_COMPLETED,
            tool_call=tool_call,
            tool_result=ToolResult(
                tool_call_id=f"c{sequence}",
                tool_name=spec.tool_name or "?",
                success=spec.success,
                error=spec.error,
                duration_ms=0.0,
            ),
        )
    if spec.type == "agent_completed":
        return AgentEvent(
            run_id=run_id,
            conversation_id="learning",
            sequence=sequence,
            type=AgentEventType.AGENT_COMPLETED,
        )
    if spec.type == "agent_failed":
        return AgentEvent(
            run_id=run_id,
            conversation_id="learning",
            sequence=sequence,
            type=AgentEventType.AGENT_FAILED,
        )
    raise ValueError(f"unsupported trace event type: {spec.type}")


async def prepare_learning_environment(
    scenario: Scenario,
    *,
    root: Path,
) -> LearningEvalEnvironment:
    """预置 Completed Tasks、Trace 事件与已有 Skill。"""

    root.mkdir(parents=True, exist_ok=True)
    task_store = FileTaskStore(root / "tasks")
    await task_store.initialize()
    trace_store = SQLiteTraceStore(root / "trace.db")
    await trace_store.initialize()
    skill_store = SkillStore(root / "user-skills", root / "project-skills")
    await skill_store.initialize()
    candidate_store = SkillCandidateStore(root / "data")
    await candidate_store.initialize()

    task_ids: list[str] = []
    aliases: dict[str, str] = {}
    for task_spec in scenario.initial_tasks:
        task = await task_store.create(
            title=task_spec.title,
            description=task_spec.description,
            goal=task_spec.goal,
            steps=tuple(
                TaskStep(
                    id=step.id,
                    title=step.title,
                    status=step.status,
                    note=step.note,
                )
                for step in task_spec.steps
            ),
            owner_conversation_id=task_spec.owner or "learning",
            run_ids=task_spec.run_ids,
        )
        if task_spec.status is not TaskStatus.PENDING:
            await task_store.set_status(task.id, task_spec.status)
        task_ids.append(task.id)
        if task_spec.alias:
            aliases[task_spec.alias] = task.id

    for run_spec in scenario.initial_runs:
        for index, event_spec in enumerate(run_spec.events):
            await trace_store.record_event(
                _trace_event(run_spec.run_id, index, event_spec)
            )

    for skill_spec in scenario.initial_skills:
        skill_root = skill_store.project_dir / skill_spec.name
        skill_root.mkdir(parents=True, exist_ok=True)
        front_matter = (
            f"---\nname: {skill_spec.name}\n"
            f"description: {skill_spec.description}\n---\n\n"
        )
        (skill_root / "SKILL.md").write_text(
            front_matter + skill_spec.body,
            encoding="utf-8",
        )

    for candidate_spec in scenario.initial_pending_candidates:
        action = SkillCandidateAction(candidate_spec.action)
        await candidate_store.create(
            SkillCandidate(
                id=uuid4().hex,
                action=action,
                proposed_name=candidate_spec.proposed_name,
                description=candidate_spec.description,
                reason=candidate_spec.reason,
                procedure=("占位步骤",),
                source_task_ids=candidate_spec.source_task_ids,
                existing_skill_name=candidate_spec.existing_skill_name,
                status=SkillCandidateStatus.PENDING,
                created_at=datetime.now(UTC),
            )
        )

    return LearningEvalEnvironment(
        root=root,
        task_store=task_store,
        trace_store=trace_store,
        skill_store=skill_store,
        candidate_store=candidate_store,
        task_ids=tuple(task_ids),
        task_aliases=aliases,
    )


async def run_learning_scenario(
    scenario: Scenario,
    *,
    root: Path,
    registry: ModelAdapterRegistry,
    provider: str = "fake",
    model: str | None = None,
    environment: LearningEvalEnvironment | None = None,
    accept_names: tuple[str, ...] = (),
    reject_names: tuple[str, ...] = (),
    accept_all: bool = False,
    reject_all: bool = False,
) -> LearningEvalOutcome:
    """预置环境并驱动 Skill Learning，可选执行 Human Gate。

    ``accept_all`` / ``reject_all`` 作用于本次产出的全部 Pending Candidate
    （Live Eval 中模型命名的 candidate 名不可预知，无法用固定名字匹配）。
    """

    env = environment or await prepare_learning_environment(scenario, root=root)
    learning = scenario.expect.learning
    settings = SkillLearningSettings(
        _env_file=None,
        skill_learning_batch_size=learning.batch_size,
        skill_learning_data_dir=env.root / "data",
    )
    service = SkillLearningService(
        env.task_store,
        env.trace_store,
        env.skill_store,
        env.candidate_store,
        registry,
        settings=settings,
        default_provider=provider,
        default_model=model,
    )
    try:
        mining = await service.maybe_run_mining()
        mining_error = getattr(mining, "error", None)
        error = mining_error if isinstance(mining_error, str) else None
    except Exception as exc:  # noqa: BLE001
        mining = None
        error = f"{type(exc).__name__}: {exc}"

    from app.skill_learning import SkillCandidateStatus

    candidates = await service.list_candidates()
    created: list[str] = []
    for candidate in list(candidates):
        if candidate.status is not SkillCandidateStatus.PENDING:
            continue
        if accept_all:
            try:
                await service.accept(candidate.id)
                created.append(candidate.proposed_name)
            except (KeyError, ValueError) as exc:
                error = error or f"{type(exc).__name__}: {exc}"
        if reject_all:
            try:
                await service.reject(candidate.id)
            except (KeyError, ValueError) as exc:
                error = error or f"{type(exc).__name__}: {exc}"
    for name in accept_names:
        matched = [c for c in candidates if c.proposed_name == name]
        if not matched:
            continue
        try:
            await service.accept(matched[0].id)
            created.append(name)
        except (KeyError, ValueError) as exc:
            error = error or f"{type(exc).__name__}: {exc}"
    for name in reject_names:
        matched = [c for c in candidates if c.proposed_name == name]
        if not matched:
            continue
        try:
            await service.reject(matched[0].id)
        except (KeyError, ValueError) as exc:
            error = error or f"{type(exc).__name__}: {exc}"

    candidates = await service.list_candidates()
    return LearningEvalOutcome(
        scenario=scenario,
        environment=env,
        mining=mining,
        candidates=candidates,
        created_skills=tuple(created),
        error=error,
    )


__all__ = [
    "LearningEvalEnvironment",
    "LearningEvalOutcome",
    "prepare_learning_environment",
    "run_learning_scenario",
]
