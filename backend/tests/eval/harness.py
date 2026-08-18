"""Eval 运行器：预置环境，驱动真实 AgentRuntime，采集结果。

- 预置 Task 到临时 tasks 目录、文件到临时 workspace、历史消息；
- 按场景配置审批门（拒绝/批准指定工具）与上下文预算覆盖（强制压缩）；
- 运行一次 ``AgentRuntime.run()``，采集 ``AgentResult``、事件与耗时；
- 不包含断言逻辑（见 ``assertions.py``），只负责"真实运行 + 采集真相"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter

from app.agent.events import AgentEvent, InMemoryEventHandler
from app.agent.result import AgentResult
from app.agent.runtime import AgentRuntime
from app.context import (
    ContextManager,
    ContextSettings,
    ConversationReducer,
    ModelContextSummarizer,
    build_model_capability_registry,
)
from app.models.config import ModelSettings
from app.models.registry import ModelAdapterRegistry
from app.models.types import Message, MessageRole
from app.skills import (
    SkillContextProvider,
    SkillSettings,
    SkillStore,
    register_skill_tools,
)
from app.task import (
    FileTaskStore,
    TaskContextProvider,
    TaskStatus,
    TaskStep,
    register_task_tools,
)
from app.tools import build_builtin_tool_registry
from app.tools.approval import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalRequest,
    ApprovalResponse,
    ApprovalScope,
)
from app.tools.registry import ToolRegistry

from .scenario import Scenario

DEFAULT_CONVERSATION_ID = "eval-conv-1"


class EvalApprovalGate(ApprovalGate):
    """按工具名决定批准/拒绝的评测审批门。"""

    def __init__(
        self,
        *,
        deny_tools: tuple[str, ...] = (),
        approve_tools: tuple[str, ...] = (),
    ) -> None:
        self._deny = set(deny_tools)
        self._approve = set(approve_tools)

    async def request_approval(self, request: ApprovalRequest) -> ApprovalResponse:
        if request.tool_name in self._deny:
            return ApprovalResponse(decision=ApprovalDecision.DENIED)
        if self._approve and request.tool_name not in self._approve:
            return ApprovalResponse(decision=ApprovalDecision.DENIED)
        return ApprovalResponse(
            decision=ApprovalDecision.APPROVED,
            scope=ApprovalScope.RUN,
        )


@dataclass
class EvalEnvironment:
    """一次运行使用到的临时环境与预置状态。"""

    root: Path
    workspace: Path
    tasks_dir: Path
    task_store: FileTaskStore
    skills_dir: Path
    skill_store: SkillStore
    conversation_id: str = DEFAULT_CONVERSATION_ID
    initial_task_ids: tuple[str, ...] = ()
    task_aliases: dict[str, str] = field(default_factory=dict)


@dataclass
class EvalOutcome:
    """一次运行的全部真实结果与采集数据。"""

    scenario: Scenario
    environment: EvalEnvironment
    result: AgentResult | None = None
    events: list[AgentEvent] = field(default_factory=list)
    duration_s: float = 0.0
    error: str | None = None


async def prepare_environment(
    scenario: Scenario,
    *,
    root: Path,
    conversation_id: str = DEFAULT_CONVERSATION_ID,
) -> EvalEnvironment:
    """创建临时目录并预置 Task、文件与任务存储。"""

    root.mkdir(parents=True, exist_ok=True)
    workspace = root / "workspace"
    tasks_dir = root / "tasks"
    skills_dir = root / "skills"
    workspace.mkdir(parents=True, exist_ok=True)
    skills_dir.mkdir(parents=True, exist_ok=True)

    for file_spec in scenario.initial_files:
        path = _resolve_within(workspace, file_spec.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(file_spec.content, encoding="utf-8")

    for skill_spec in scenario.initial_skills:
        skill_root = skills_dir / skill_spec.name
        skill_root.mkdir(parents=True, exist_ok=True)
        front_matter = (
            f"---\nname: {skill_spec.name}\n"
            f"description: {skill_spec.description}\n---\n\n"
        )
        (skill_root / "SKILL.md").write_text(
            front_matter + skill_spec.body,
            encoding="utf-8",
        )
        for reference in skill_spec.reference_files:
            ref_path = _resolve_within(skill_root, reference.path)
            ref_path.parent.mkdir(parents=True, exist_ok=True)
            ref_path.write_text(reference.content, encoding="utf-8")

    task_store = FileTaskStore(tasks_dir)
    await task_store.initialize()
    skill_store = SkillStore(
        user_dir=skills_dir,
        project_dir=skills_dir,
        settings=SkillSettings(),
    )
    await skill_store.initialize()
    initial_task_ids: list[str] = []
    task_aliases: dict[str, str] = {}
    for task_spec in scenario.initial_tasks:
        task = await task_store.create(
            title=task_spec.title,
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
            owner_conversation_id=task_spec.owner or conversation_id,
        )
        if task_spec.status is not TaskStatus.PENDING:
            await task_store.set_status(task.id, task_spec.status)
        initial_task_ids.append(task.id)
        if task_spec.alias:
            task_aliases[task_spec.alias] = task.id
    return EvalEnvironment(
        root=root,
        workspace=workspace,
        tasks_dir=tasks_dir,
        task_store=task_store,
        skills_dir=skills_dir,
        skill_store=skill_store,
        conversation_id=conversation_id,
        initial_task_ids=tuple(initial_task_ids),
        task_aliases=task_aliases,
    )


def build_runtime(
    scenario: Scenario,
    environment: EvalEnvironment,
    *,
    provider: str | None = None,
    model: str | None = None,
    registry: ModelAdapterRegistry | None = None,
) -> AgentRuntime:
    """按场景配置构造真实 AgentRuntime；可注入 mock 注册表。"""

    tool_registry = build_builtin_tool_registry(environment.workspace)
    register_task_tools(tool_registry, environment.task_store)
    register_skill_tools(tool_registry, environment.skill_store)
    _validate_tool_names(tool_registry, scenario)
    _apply_allowed_tools(tool_registry, scenario.allowed_tools)

    approval_gate = EvalApprovalGate(
        deny_tools=scenario.approval.deny_tools,
        approve_tools=scenario.approval.approve_tools,
    )
    resolved_registry = registry or ModelAdapterRegistry(ModelSettings())
    context_manager = _build_context_manager(
        scenario,
        registry=resolved_registry,
        provider=provider,
        model=model,
    )
    skill_settings = SkillSettings()

    return AgentRuntime(
        resolved_registry,
        tool_registry,
        provider=provider,
        model=model,
        max_steps=scenario.max_steps,
        max_tool_rounds=scenario.max_tool_rounds,
        max_output_tokens=scenario.max_output_tokens,
        approval_gate=approval_gate,
        context_manager=context_manager,
        task_context_provider=TaskContextProvider(environment.task_store),
        skill_store=environment.skill_store,
        skill_context_provider=SkillContextProvider(
            max_tokens=skill_settings.skill_context_max_tokens,
            max_active=skill_settings.skill_max_active,
        ),
    )


async def run_scenario(
    scenario: Scenario,
    *,
    root: Path,
    provider: str | None = None,
    model: str | None = None,
    conversation_id: str = DEFAULT_CONVERSATION_ID,
    registry: ModelAdapterRegistry | None = None,
) -> EvalOutcome:
    """预置环境并真实运行一次场景，返回采集结果。"""

    environment = await prepare_environment(
        scenario,
        root=root,
        conversation_id=conversation_id,
    )
    runtime = build_runtime(
        scenario,
        environment,
        provider=provider,
        model=model,
        registry=registry,
    )
    handler = InMemoryEventHandler()
    history = _messages_from_history(scenario.initial_history)
    started = perf_counter()
    try:
        result = await runtime.run(
            scenario.user_input,
            history=history,
            conversation_id=environment.conversation_id,
            event_handler=handler,
        )
        error: str | None = None
    except Exception as exc:  # noqa: BLE001 - 评测运行异常统一采集
        result = None
        error = f"{type(exc).__name__}: {exc}"
    duration_s = perf_counter() - started
    return EvalOutcome(
        scenario=scenario,
        environment=environment,
        result=result,
        events=list(handler.events),
        duration_s=duration_s,
        error=error,
    )


def _messages_from_history(
    history: tuple,
) -> tuple[Message, ...]:
    return tuple(
        Message(
            role=MessageRole(message.role),
            content=message.content,
        )
        for message in history
    )


def _apply_allowed_tools(
    registry: ToolRegistry,
    allowed: tuple[str, ...] | None,
) -> None:
    if allowed is None:
        return
    allowed_set = set(allowed)
    for name in registry.names():
        if name not in allowed_set:
            registry.unregister(name)


def _validate_tool_names(registry: ToolRegistry, scenario: Scenario) -> None:
    """在调用模型前拒绝场景中的未知工具名。"""

    expect = scenario.expect.tools
    declared = (
        set(expect.must)
        | set(expect.must_not)
        | set(expect.successful)
        | set(expect.unsuccessful)
        | set(expect.no_successful)
        | set(expect.args)
        | set(expect.count)
        | set(expect.ordered)
        | set(expect.approval_denied)
        | set(scenario.approval.deny_tools)
        | set(scenario.approval.approve_tools)
        | set(scenario.allowed_tools or ())
    )
    unknown = sorted(declared - set(registry.names()))
    if unknown:
        raise ValueError(f"评测场景包含未注册工具：{unknown}")


def _build_context_manager(
    scenario: Scenario,
    *,
    registry: ModelAdapterRegistry,
    provider: str | None,
    model: str | None,
) -> ContextManager:
    overrides = scenario.context
    if (
        overrides.window_override is None
        and overrides.margin_tokens is None
        and overrides.keep_recent_conversation_blocks is None
        and not scenario.expect.requires_compaction
    ):
        return ContextManager()
    kwargs: dict = {"_env_file": None}
    if overrides.window_override is not None:
        kwargs["context_window_override"] = overrides.window_override
    if overrides.margin_tokens is not None:
        kwargs["context_safety_margin_tokens"] = overrides.margin_tokens
    settings = ContextSettings(**kwargs)
    capability_registry = build_model_capability_registry(
        context_settings=settings
    )
    adapter = registry.get(provider)
    resolved_provider = adapter.provider
    resolved_model = model or adapter.default_model
    if overrides.window_override is not None:
        capability_registry.register_override(
            resolved_provider,
            resolved_model,
            context_window=overrides.window_override,
        )
    conversation_reducer = None
    if scenario.expect.requires_compaction:
        summarizer = ModelContextSummarizer(
            registry,
            provider=resolved_provider,
            model=resolved_model,
            max_output_tokens=settings.context_summary_max_output_tokens,
        )
        conversation_reducer = ConversationReducer(
            summarizer,
            keep_recent_conversation_blocks=(
                overrides.keep_recent_conversation_blocks
                if overrides.keep_recent_conversation_blocks is not None
                else settings.context_keep_recent_conversation_blocks
            ),
            keep_recent_tool_rounds=settings.context_keep_recent_tool_rounds,
        )
    return ContextManager(
        registry=capability_registry,
        context_settings=settings,
        conversation_reducer=conversation_reducer,
    )


def _resolve_within(root: Path, relative_path: str) -> Path:
    """解析评测文件路径，并拒绝绝对路径与目录穿越。"""

    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"评测文件路径超出 workspace：{relative_path}") from exc
    return candidate


__all__ = [
    "DEFAULT_CONVERSATION_ID",
    "EvalApprovalGate",
    "EvalEnvironment",
    "EvalOutcome",
    "build_runtime",
    "prepare_environment",
    "run_scenario",
]
