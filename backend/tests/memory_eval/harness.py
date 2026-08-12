"""长期记忆多阶段 Runner：共享 Store，隔离会话历史，采集完整现场。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter

from app.agent.events import AgentEvent, InMemoryEventHandler
from app.agent.result import AgentResult
from app.agent.runtime import AgentRuntime
from app.memory import (
    MemoryMaintenanceConfig,
    MemoryMaintenanceReflector,
    MemoryManager,
    MemoryRecord,
    MemoryReflectionConfig,
    PostRunMemoryReflector,
    register_memory_tools,
)
from app.models.config import ModelSettings
from app.models.registry import ModelAdapterRegistry
from app.models.types import Message
from app.tools.registry import ToolRegistry

from .scenario import InitialMemoryStatus, MemoryEvalPhase, MemoryEvalScenario


@dataclass(frozen=True)
class MemorySnapshot:
    """一个阶段结束后的 Memory 可审计快照。"""

    core: str
    index: str
    active: tuple[MemoryRecord, ...]
    archived: tuple[MemoryRecord, ...]


@dataclass
class MemoryEvalPhaseOutcome:
    """单阶段运行结果、事件与存储快照。"""

    phase: MemoryEvalPhase
    result: AgentResult | None = None
    events: list[AgentEvent] = field(default_factory=list)
    snapshot: MemorySnapshot | None = None
    duration_s: float = 0.0
    error: str | None = None


@dataclass
class MemoryEvalOutcome:
    """整条多阶段场景的真实执行现场。"""

    scenario: MemoryEvalScenario
    root: Path
    manager: MemoryManager
    aliases: dict[str, str] = field(default_factory=dict)
    phases: list[MemoryEvalPhaseOutcome] = field(default_factory=list)


async def run_scenario(
    scenario: MemoryEvalScenario,
    *,
    root: Path,
    provider: str | None = None,
    model: str | None = None,
    registry: ModelAdapterRegistry | None = None,
    memory_enabled: bool = True,
) -> MemoryEvalOutcome:
    """按阶段运行场景；同一 conversation 复用历史，不同会话只共享 Memory。"""

    root.mkdir(parents=True, exist_ok=True)
    manager = MemoryManager(root / "memory", max_active=scenario.max_active)
    await manager.initialize()
    aliases = await _seed_memory(manager, scenario)
    resolved_registry = registry or ModelAdapterRegistry(ModelSettings())
    histories: dict[str, tuple[Message, ...]] = {}
    outcome = MemoryEvalOutcome(
        scenario=scenario,
        root=root,
        manager=manager,
        aliases=aliases,
    )

    for phase in scenario.phases:
        handler = InMemoryEventHandler()
        runtime = _build_runtime(
            phase,
            manager=manager,
            registry=resolved_registry,
            provider=provider,
            model=model,
            memory_enabled=memory_enabled,
        )
        started = perf_counter()
        try:
            result = await runtime.run(
                phase.user_input,
                history=histories.get(phase.conversation, ()),
                conversation_id=f"memory-eval-{phase.conversation}",
                event_handler=handler,
            )
            histories[phase.conversation] = result.messages
            error: str | None = None
        except Exception as exc:  # noqa: BLE001 - 评测统一采集异常
            result = None
            error = f"{type(exc).__name__}: {exc}"
        phase_outcome = MemoryEvalPhaseOutcome(
            phase=phase,
            result=result,
            events=list(handler.events),
            snapshot=await snapshot_memory(manager),
            duration_s=perf_counter() - started,
            error=error,
        )
        outcome.phases.append(phase_outcome)
        _bind_reflection_memory(phase_outcome, aliases)
        await _write_phase_artifacts(root, phase_outcome)
        if error is not None:
            break
    return outcome


def _build_runtime(
    phase: MemoryEvalPhase,
    *,
    manager: MemoryManager,
    registry: ModelAdapterRegistry,
    provider: str | None,
    model: str | None,
    memory_enabled: bool,
) -> AgentRuntime:
    tools = ToolRegistry()
    reflector = None
    maintainer = None
    runtime_manager = None
    if memory_enabled:
        register_memory_tools(tools, manager)
        reflection_config = MemoryReflectionConfig(
            _env_file=None,
            provider=provider,
            model=model,
            capture_raw_io=True,
        )
        reflector = PostRunMemoryReflector(
            registry,
            config=reflection_config,
            default_provider=provider,
            default_model=model,
        )
        maintenance_config = MemoryMaintenanceConfig(
            _env_file=None,
            provider=provider,
            model=model,
        )
        maintainer = MemoryMaintenanceReflector(
            registry,
            config=maintenance_config,
            default_provider=provider,
            default_model=model,
        )
        runtime_manager = manager
    return AgentRuntime(
        registry,
        tools,
        provider=provider,
        model=model,
        max_steps=phase.max_steps,
        max_tool_rounds=phase.max_tool_rounds,
        memory_manager=runtime_manager,
        memory_reflector=reflector,
        memory_maintenance_reflector=maintainer,
    )


async def _seed_memory(
    manager: MemoryManager,
    scenario: MemoryEvalScenario,
) -> dict[str, str]:
    aliases: dict[str, str] = {}
    if scenario.initial_core.strip():
        await manager.core.update(scenario.initial_core)
    for spec in scenario.initial_memories:
        record = await manager.create(
            title=spec.title,
            summary=spec.summary,
            content=spec.content,
        )
        aliases[spec.alias] = record.id
        if spec.status is InitialMemoryStatus.ARCHIVED:
            await manager.archive(record.id, reason=spec.archive_reason)
    return aliases


def _bind_reflection_memory(
    phase_outcome: MemoryEvalPhaseOutcome,
    aliases: dict[str, str],
) -> None:
    alias = phase_outcome.phase.bind_reflection_memory_as
    if alias is None:
        return
    memory_ids = [
        event.reflection_memory_id
        for event in phase_outcome.events
        if event.reflection_memory_id is not None
        and event.reflection_mutation_applied is True
    ]
    if len(memory_ids) == 1:
        aliases[alias] = memory_ids[0]


async def snapshot_memory(manager: MemoryManager) -> MemorySnapshot:
    active = await manager.store.list_active()
    archived: list[MemoryRecord] = []
    for path in sorted(manager.store.archive_dir.glob("M*.md")):
        record = await manager.store.load(path.stem)
        if record is not None:
            archived.append(record)
    return MemorySnapshot(
        core=await manager.core.load(),
        index=await manager.index.load() or "",
        active=active,
        archived=tuple(archived),
    )


async def _write_phase_artifacts(
    root: Path,
    outcome: MemoryEvalPhaseOutcome,
) -> None:
    """保存 Reflection 原始 I/O 与阶段摘要，便于失败后直接复盘。"""

    reflection_events = [
        event
        for event in outcome.events
        if event.reflection_input_json is not None
        or event.reflection_raw_output is not None
    ]
    reflection = reflection_events[-1] if reflection_events else None
    payload = {
        "phase_id": outcome.phase.id,
        "conversation": outcome.phase.conversation,
        "user_input": outcome.phase.user_input,
        "final_answer": (
            outcome.result.content if outcome.result is not None else None
        ),
        "reflection": (
            {
                "event_type": reflection.type.value,
                "input": _parse_json_or_text(reflection.reflection_input_json),
                "raw_output": reflection.reflection_raw_output,
                "action": reflection.reflection_action,
                "memory_id": reflection.reflection_memory_id,
                "mutation_applied": reflection.reflection_mutation_applied,
                "error": reflection.reflection_error,
            }
            if reflection is not None
            else None
        ),
    }
    path = root / "artifacts" / f"{outcome.phase.id}.json"
    await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"
    await asyncio.to_thread(path.write_text, content, encoding="utf-8")


def _parse_json_or_text(value: str | None) -> object:
    if value is None:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


__all__ = [
    "MemoryEvalOutcome",
    "MemoryEvalPhaseOutcome",
    "MemorySnapshot",
    "run_scenario",
    "snapshot_memory",
]
