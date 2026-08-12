"""长期记忆多阶段场景的确定性断言。"""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.agent.events import AgentEventType

from .harness import MemoryEvalPhaseOutcome
from .scenario import MemoryEvalScenario


@dataclass(frozen=True)
class MemoryCheckResult:
    """一条可解释的 Memory Eval 检查结果。"""

    name: str
    ok: bool
    detail: str


def check_phase(
    scenario: MemoryEvalScenario,
    phase_outcome: MemoryEvalPhaseOutcome,
    *,
    aliases: dict[str, str],
    mode: str = "on",
) -> tuple[list[MemoryCheckResult], bool]:
    """检查阶段行为、回答与 Store 快照。"""

    if phase_outcome.error is not None or phase_outcome.result is None:
        check = MemoryCheckResult(
            "ran_ok",
            False,
            phase_outcome.error or "AgentResult 缺失",
        )
        return [check], False
    expect = (
        phase_outcome.phase.expect_off
        if mode == "off" and phase_outcome.phase.expect_off is not None
        else phase_outcome.phase.expect
    )
    if mode == "off" and phase_outcome.phase.expect_off is None:
        check = MemoryCheckResult(
            "ran_ok",
            phase_outcome.result.ok,
            "Memory OFF 对照未声明行为断言，仅采集成本与回答",
        )
        return [check], check.ok
    result = phase_outcome.result
    snapshot = phase_outcome.snapshot
    checks = [MemoryCheckResult("ran_ok", result.ok, result.stop_reason.value)]

    reflection_events = [
        event
        for event in phase_outcome.events
        if event.type is AgentEventType.MEMORY_REFLECTION_COMPLETED
    ]
    actual_reflection = (
        reflection_events[-1].reflection_action if reflection_events else None
    )
    if expect.reflection_action is not None:
        checks.append(
            MemoryCheckResult(
                "reflection_action",
                actual_reflection == expect.reflection_action,
                f"actual={actual_reflection} expected={expect.reflection_action}",
            )
        )
    if expect.reflection_mutation_applied is not None:
        actual_applied = (
            reflection_events[-1].reflection_mutation_applied
            if reflection_events
            else None
        )
        checks.append(
            MemoryCheckResult(
                "reflection_mutation",
                actual_applied is expect.reflection_mutation_applied,
                f"actual={actual_applied} "
                f"expected={expect.reflection_mutation_applied}",
            )
        )

    maintenance = [
        event
        for event in phase_outcome.events
        if event.type is AgentEventType.MEMORY_MAINTENANCE_COMPLETED
    ]
    if expect.maintenance_action is not None:
        actual = maintenance[-1].maintenance_action if maintenance else None
        checks.append(
            MemoryCheckResult(
                "maintenance_action",
                actual == expect.maintenance_action,
                f"actual={actual} expected={expect.maintenance_action}",
            )
        )

    reads = _memory_reads(result.tool_calls)
    expected_reads = [_resolve_alias(value, aliases) for value in expect.recalled]
    forbidden_reads = [
        _resolve_alias(value, aliases) for value in expect.not_recalled
    ]
    if expect.recalled or expect.not_recalled:
        missing = [value for value in expected_reads if value not in reads]
        forbidden = [value for value in forbidden_reads if value in reads]
        checks.append(
            MemoryCheckResult(
                "recall",
                not missing and not forbidden,
                f"reads={reads} missing={missing} forbidden={forbidden}",
            )
        )
    if expect.total_memory_reads is not None:
        checks.append(
            MemoryCheckResult(
                "read_count",
                len(reads) == expect.total_memory_reads,
                f"actual={len(reads)} expected={expect.total_memory_reads}",
            )
        )

    answer = result.content or ""
    missing_answer = [value for value in expect.answer.contains if value not in answer]
    forbidden_answer = [value for value in expect.answer.excludes if value in answer]
    if expect.answer.contains or expect.answer.excludes:
        checks.append(
            MemoryCheckResult(
                "answer",
                not missing_answer and not forbidden_answer,
                f"missing={missing_answer} forbidden={forbidden_answer}",
            )
        )

    if snapshot is not None:
        if expect.active_count is not None:
            checks.append(
                MemoryCheckResult(
                    "active_count",
                    len(snapshot.active) == expect.active_count,
                    f"actual={len(snapshot.active)} expected={expect.active_count}",
                )
            )
        if expect.archive_count is not None:
            checks.append(
                MemoryCheckResult(
                    "archive_count",
                    len(snapshot.archived) == expect.archive_count,
                    f"actual={len(snapshot.archived)} expected={expect.archive_count}",
                )
            )
        if expect.core_contains:
            missing_core = [
                value for value in expect.core_contains if value not in snapshot.core
            ]
            checks.append(
                MemoryCheckResult(
                    "core",
                    not missing_core,
                    f"missing={missing_core}",
                )
            )
        if expect.memory is not None:
            target_id = _resolve_alias(expect.memory.target, aliases)
            record = next(
                (
                    item
                    for item in (*snapshot.active, *snapshot.archived)
                    if item.id == target_id
                ),
                None,
            )
            failures: list[str] = []
            if record is None:
                failures.append(f"not_found={target_id}")
            else:
                for value in expect.memory.title_contains:
                    if value not in record.title:
                        failures.append(f"title_missing={value}")
                for value in expect.memory.summary_contains:
                    if value not in record.summary:
                        failures.append(f"summary_missing={value}")
                for value in expect.memory.content_contains:
                    if value not in record.content:
                        failures.append(f"content_missing={value}")
                minimum = expect.memory.revision_at_least
                if minimum is not None and record.revision < minimum:
                    failures.append(f"revision={record.revision} < {minimum}")
            checks.append(
                MemoryCheckResult(
                    "stored_memory",
                    not failures,
                    ", ".join(failures) or f"target={target_id}",
                )
            )
    return checks, all(check.ok for check in checks)


def _memory_reads(records: tuple) -> list[str]:
    ids: list[str] = []
    for record in records:
        if record.tool_call.name != "memory_read" or not record.result.success:
            continue
        arguments = record.tool_call.arguments
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                continue
        if isinstance(arguments, dict) and isinstance(arguments.get("memory_id"), str):
            ids.append(arguments["memory_id"].strip().upper())
    return ids


def _resolve_alias(value: str, aliases: dict[str, str]) -> str:
    return aliases.get(value, value).strip().upper()


__all__ = ["MemoryCheckResult", "check_phase"]
