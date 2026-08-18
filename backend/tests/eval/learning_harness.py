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
from app.skill_learning.evidence import TraceEvidenceBuilder
from app.skill_learning.trace_selector import TaskTraceSelector
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
    # 确定性 Trace 诊断（用生产 TaskTraceSelector + TraceEvidenceBuilder 计算）：
    #   trace_steps_by_alias: {alias: {run_id: tuple[int, ...]}}
    #                         （选中的 Agent Step，去重保序）
    #   evidence_by_alias:    {alias: evidence 文本}
    trace_steps_by_alias: dict[str, dict[str, tuple[int, ...]]] = field(
        default_factory=dict
    )
    evidence_by_alias: dict[str, str] = field(default_factory=dict)


def _task_step_note(step) -> str | None:
    """场景 steps 常省略 note，但生产 TaskStep 要求 done/blocked 必须有 note。

    Eval 预置时对缺失 note 的终态步骤补默认值（不改生产 schema；旧场景自带
    note 不受影响）。
    """

    if step.note:
        return step.note
    from app.task import TaskStepStatus

    if step.status is TaskStepStatus.DONE:
        return "已完成"
    if step.status is TaskStepStatus.BLOCKED:
        return "等待外部条件"
    return None


def _resolve_task_aliases(value: object, aliases: dict[str, str]) -> object:
    """递归把 Trace arguments 中"整个字符串就是 $task:<alias>"的值替换为真实 Task ID。

    只替换完整匹配；alias 不存在时明确报错，不静默保留。
    """

    if isinstance(value, str) and value.startswith("$task:"):
        alias = value[len("$task:"):]
        if alias not in aliases:
            raise ValueError(f"unknown task alias in trace arguments: {alias!r}")
        return aliases[alias]
    if isinstance(value, dict):
        return {
            key: _resolve_task_aliases(item, aliases)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_resolve_task_aliases(item, aliases) for item in value]
    if isinstance(value, tuple):
        return tuple(_resolve_task_aliases(item, aliases) for item in value)
    return value


def _trace_event(run_id: str, sequence: int, spec, aliases) -> AgentEvent:
    """把 InitialTraceEvent 转为 AgentEvent（透传 step，解析 $task:<alias>）。"""

    arguments = (
        _resolve_task_aliases(spec.arguments, aliases) if aliases else spec.arguments
    )
    tool_call = (
        ToolCall(id=f"c{sequence}", name=spec.tool_name, arguments=arguments)
        if spec.tool_name
        else None
    )
    if spec.type == "tool_started":
        return AgentEvent(
            run_id=run_id,
            conversation_id="learning",
            sequence=sequence,
            step=spec.step,
            type=AgentEventType.TOOL_STARTED,
            tool_call=tool_call,
        )
    if spec.type == "tool_completed":
        return AgentEvent(
            run_id=run_id,
            conversation_id="learning",
            sequence=sequence,
            step=spec.step,
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
                    note=_task_step_note(step),
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
                _trace_event(run_spec.run_id, index, event_spec, aliases)
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
    # Human Gate 机制测试：预置 Candidate，不跑真实模型产候选，
    # 避免 Distiller 偶发返回 none 把机制本身判失败。
    mining = None
    error = None
    if not learning.human_gate_only:
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
    trace_steps_by_alias, evidence_by_alias = await _build_trace_diagnostics(
        scenario, env, settings
    )
    return LearningEvalOutcome(
        scenario=scenario,
        environment=env,
        mining=mining,
        candidates=candidates,
        created_skills=tuple(created),
        error=error,
        trace_steps_by_alias=trace_steps_by_alias,
        evidence_by_alias=evidence_by_alias,
    )


async def _build_trace_diagnostics(
    scenario: Scenario,
    env: LearningEvalEnvironment,
    settings: SkillLearningSettings,
) -> tuple[dict[str, dict[str, tuple[int, ...]]], dict[str, str]]:
    """用生产 TaskTraceSelector + TraceEvidenceBuilder 计算确定性 Trace 诊断。

    只统计带 alias 的 Task：选中 Agent Step 按 run 去重保序，并生成该 Task 的
    Evidence 文本（受 max_evidence_chars 限制）。不复制一套 Selector 算法。
    """

    selector = TaskTraceSelector()
    builder = TraceEvidenceBuilder(settings)
    trace_steps_by_alias: dict[str, dict[str, tuple[int, ...]]] = {}
    evidence_by_alias: dict[str, str] = {}
    for spec in scenario.initial_tasks:
        if not spec.alias:
            continue
        task_id = env.task_aliases.get(spec.alias)
        if task_id is None:
            continue
        task = await env.task_store.get(task_id)
        if task is None:
            continue
        run_events: dict[str, tuple] = {}
        for run_id in task.run_ids:
            try:
                run_events[run_id] = await env.trace_store.load_events(run_id)
            except (KeyError, ValueError, OSError):
                continue
        if not run_events:
            continue
        selected = selector.select(
            task,
            run_events,
            max_events=settings.skill_learning_max_events_per_task,
        )
        steps_by_run: dict[str, list[int]] = {}
        for event in selected:
            if event.step is None:
                continue
            steps = steps_by_run.setdefault(event.run_id, [])
            if event.step not in steps:
                steps.append(event.step)
        trace_steps_by_alias[spec.alias] = {
            run_id: tuple(steps) for run_id, steps in steps_by_run.items()
        }
        evidence_by_alias[spec.alias] = builder.build(task, selected)
    return trace_steps_by_alias, evidence_by_alias


__all__ = [
    "LearningEvalEnvironment",
    "LearningEvalOutcome",
    "prepare_learning_environment",
    "run_learning_scenario",
]
