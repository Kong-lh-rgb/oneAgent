"""场景期望的评分：工具轨迹、Task 状态、文件、回答关键点、压缩触发。

每个检查函数返回 ``(ok, detail)``，供 metrics 汇总与失败归因。
工具断言默认宽松：只检查必须包含、禁止包含、参数关键值。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.agent.events import AgentEvent, AgentEventType
from app.agent.result import AgentResult
from app.task import Task, TaskStepStatus

from .scenario import Scenario

_CHECK_NAMES = (
    "tools",
    "task",
    "skill",
    "files",
    "answer",
    "compaction",
    "ran_ok",
)


@dataclass
class CheckResult:
    """一条检查项的评分结果。"""

    name: str
    ok: bool
    detail: str = ""
    applicable: bool = True


async def run_checks(
    scenario: Scenario,
    *,
    outcome: Any,
) -> tuple[list[CheckResult], bool]:
    """运行全部检查，返回逐项结果与整体是否通过。"""

    checks: list[CheckResult] = []
    if outcome.error is not None or outcome.result is None:
        checks.append(
            CheckResult(
                "ran_ok",
                False,
                f"运行失败：{outcome.error or 'result is None'}",
            )
        )
        return checks, False

    result: AgentResult = outcome.result
    allowed_stops = scenario.expect.stop_reason_any
    stop_ok = result.stop_reason in allowed_stops
    checks.append(
        CheckResult(
            "ran_ok",
            stop_ok,
            f"stop={result.stop_reason.value}; "
            f"expected={[reason.value for reason in allowed_stops]}",
        )
    )
    checks.append(_check_tools(scenario, result, outcome.events))
    checks.append(_check_skill(scenario, outcome.events))
    checks.append(
        await _check_task(
            scenario,
            outcome.environment,
        )
    )
    checks.append(_check_files(scenario, outcome.environment.workspace))
    checks.append(_check_answer(scenario, result))
    checks.append(_check_compaction(scenario, outcome.events))
    return checks, all(check.ok for check in checks)


def _check_tools(
    scenario: Scenario,
    result: AgentResult,
    events: list[AgentEvent],
) -> CheckResult:
    called = [record.tool_call.name for record in result.tool_calls]
    expect = scenario.expect.tools

    applicable = any(
        (
            expect.must,
            expect.must_not,
            expect.successful,
            expect.unsuccessful,
            expect.no_successful,
            expect.args,
            expect.count,
            expect.total_count is not None,
            expect.ordered,
            expect.approval_denied,
        )
    )
    if not applicable:
        return CheckResult("tools", True, "未声明工具期望", applicable=False)

    missing = [name for name in expect.must if name not in called]
    forbidden_hit = [name for name in expect.must_not if name in called]
    no_success_hit = [
        name
        for name in expect.no_successful
        if any(
            record.tool_call.name == name and record.result.success
            for record in result.tool_calls
        )
    ]
    missing_success = [
        name
        for name in expect.successful
        if not any(
            record.tool_call.name == name and record.result.success
            for record in result.tool_calls
        )
    ]
    missing_failure = [
        name
        for name in expect.unsuccessful
        if not any(
            record.tool_call.name == name and not record.result.success
            for record in result.tool_calls
        )
    ]
    count_failures = [
        f"{name}={called.count(name)} 期望 {expected_count}"
        for name, expected_count in expect.count.items()
        if called.count(name) != expected_count
    ]
    total_count_ok = (
        expect.total_count is None or len(called) == expect.total_count
    )
    order_ok = _is_ordered_subsequence(expect.ordered, called)
    denied_tools = {
        event.tool_call.name
        for event in events
        if event.type is AgentEventType.TOOL_APPROVAL_COMPLETED
        and event.tool_call is not None
        and event.approval_decision is not None
        and event.approval_decision.value == "denied"
    }
    missing_denials = [
        name for name in expect.approval_denied if name not in denied_tools
    ]

    args_ok, args_detail = _check_tool_args(expect.args, result.tool_calls)
    detail_parts = [f"called={called}"]
    if missing:
        detail_parts.append(f"missing={missing}")
    if forbidden_hit:
        detail_parts.append(f"forbidden_called={forbidden_hit}")
    if no_success_hit:
        detail_parts.append(f"should_not_succeed={no_success_hit}")
    if missing_success:
        detail_parts.append(f"missing_success={missing_success}")
    if missing_failure:
        detail_parts.append(f"missing_failure={missing_failure}")
    if count_failures:
        detail_parts.append(f"count_failures={count_failures}")
    if not total_count_ok:
        detail_parts.append(
            f"total_count={len(called)} 期望 {expect.total_count}"
        )
    if not order_ok:
        detail_parts.append(f"order={called} 未包含有序序列 {list(expect.ordered)}")
    if missing_denials:
        detail_parts.append(f"missing_approval_denials={missing_denials}")
    failed_results = [
        f"{record.tool_call.name}: {record.result.error}"
        for record in result.tool_calls
        if not record.result.success and record.result.error
    ]
    if failed_results:
        detail_parts.append(f"failed_results={failed_results}")
    if args_detail:
        detail_parts.append(args_detail)
    return CheckResult(
        "tools",
        not missing
        and not forbidden_hit
        and not no_success_hit
        and not missing_success
        and not missing_failure
        and not count_failures
        and total_count_ok
        and order_ok
        and not missing_denials
        and args_ok,
        "; ".join(detail_parts),
    )


def _check_skill(
    scenario: Scenario,
    events: list[AgentEvent],
) -> CheckResult:
    """检查 Skill 是否按预期激活 / 不激活 / 激活失败。"""

    expect = scenario.expect.skill
    applicable = any(
        (
            expect.activated,
            expect.not_activated,
            expect.activation_failed,
        )
    )
    if not applicable:
        return CheckResult("skill", True, "未声明 Skill 期望", applicable=False)

    activated = {
        event.skill_name
        for event in events
        if event.type is AgentEventType.SKILL_ACTIVATED
        and event.skill_name is not None
    }
    failed = {
        event.skill_name
        for event in events
        if event.type is AgentEventType.SKILL_ACTIVATION_FAILED
        and event.skill_name is not None
    }

    missing_activated = [
        name for name in expect.activated if name not in activated
    ]
    wrongly_activated = [
        name for name in expect.not_activated if name in activated
    ]
    missing_failed = [
        name for name in expect.activation_failed if name not in failed
    ]

    compaction_ok = True
    if expect.survives_compaction:
        started = [
            event for event in events if event.type is AgentEventType.MODEL_STARTED
        ]
        compacted_indexes = [
            index
            for index, event in enumerate(started)
            if event.compaction_stage not in (None, "none")
        ]
        if not compacted_indexes:
            compaction_ok = False
        else:
            last_compacted = compacted_indexes[-1]
            after = started[last_compacted + 1 :]
            # 只依赖 run state 的 active_skill_names 不够：这里要求压缩之后的
            # 实际 ModelRequest 仍然注入了 vesta_active_skill 消息，并且
            # 该消息对应声明要激活的 Skill。
            wanted = set(expect.activated)
            compaction_ok = any(
                bool(event.active_skill_message_names)
                and (not wanted or bool(set(event.active_skill_message_names) & wanted))
                for event in after
            )

    detail_parts = [f"activated={sorted(activated)}"]
    if missing_activated:
        detail_parts.append(f"missing_activated={missing_activated}")
    if wrongly_activated:
        detail_parts.append(f"wrongly_activated={wrongly_activated}")
    if missing_failed:
        detail_parts.append(f"missing_activation_failed={missing_failed}")
    if expect.survives_compaction and not compaction_ok:
        detail_parts.append("active skill 未在压缩后保留")
    return CheckResult(
        "skill",
        not missing_activated
        and not wrongly_activated
        and not missing_failed
        and compaction_ok,
        "; ".join(detail_parts),
    )


def _check_tool_args(
    expected: dict[str, dict[str, object]],
    records: tuple,
) -> tuple[bool, str]:
    """宽松参数检查：只验证期望的关键字段存在且相等（不比对全部）。"""

    if not expected:
        return True, ""
    actual: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        actual.setdefault(record.tool_call.name, []).append(
            dict(record.tool_call.arguments or {})
        )

    failures: list[str] = []
    for tool_name, key_values in expected.items():
        candidates = actual.get(tool_name, [])
        if not candidates:
            failures.append(f"{tool_name} 未被调用")
            continue
        if not any(
            all(arguments.get(key) == value for key, value in key_values.items())
            for arguments in candidates
        ):
            failures.append(
                f"{tool_name} 没有一次调用匹配参数 {key_values!r}; "
                f"actual={candidates!r}"
            )
    return (not failures), (f"args_failures={failures}" if failures else "")


def _is_ordered_subsequence(expected: tuple[str, ...], actual: list[str]) -> bool:
    if not expected:
        return True
    position = 0
    for name in actual:
        if name == expected[position]:
            position += 1
            if position == len(expected):
                return True
    return False


async def _check_task(
    scenario: Scenario,
    environment: Any,
) -> CheckResult:
    expect = scenario.expect.task
    applicable = any(
        (
            expect.created is not None,
            expect.new_count is not None,
            expect.target is not None,
            expect.status_any,
            expect.title_contains,
            expect.goal_contains,
            expect.content_contains,
            expect.min_steps is not None,
            expect.steps,
            expect.all_steps_done is not None,
        )
    )
    if not applicable:
        return CheckResult("task", True, "未声明 Task 期望", applicable=False)

    tasks = await environment.task_store.list(
        owner_conversation_id=environment.conversation_id
    )
    by_id = {task.id: task for task in tasks}
    initial_ids = set(environment.initial_task_ids)
    new_tasks = [task for task in tasks if task.id not in initial_ids]

    failures: list[str] = []
    if expect.created is True and not new_tasks:
        failures.append("期望新增 Task，但新增数量为 0")
    if expect.created is False and new_tasks:
        failures.append(f"不应新增 Task，但新增了 {len(new_tasks)} 个")
    if expect.new_count is not None and len(new_tasks) != expect.new_count:
        failures.append(
            f"new_tasks={len(new_tasks)} 期望 {expect.new_count}"
        )

    needs_target = any(
        (
            expect.target is not None,
            expect.status_any,
            expect.title_contains,
            expect.goal_contains,
            expect.content_contains,
            expect.min_steps is not None,
            expect.steps,
            expect.all_steps_done is not None,
        )
    )
    task: Task | None = None
    if needs_target:
        if expect.target == "new":
            if len(new_tasks) == 1:
                task = new_tasks[0]
            elif len(new_tasks) != 1:
                failures.append(
                    f"target=new 需要恰好一个新增 Task，实际 {len(new_tasks)}"
                )
        elif expect.target is not None:
            task_id = environment.task_aliases.get(expect.target)
            task = by_id.get(task_id) if task_id else None
            if task is None:
                failures.append(f"找不到目标 Task 别名：{expect.target}")
        elif len(new_tasks) == 1 and expect.created is True:
            task = new_tasks[0]
        elif len(initial_ids) == 1:
            task = by_id.get(next(iter(initial_ids)))
        elif len(tasks) == 1:
            task = tasks[0]
        else:
            failures.append("存在多个 Task，必须用 target 指定检查对象")

    if needs_target and task is None:
        return CheckResult("task", False, "; ".join(failures))
    if task is None:
        return CheckResult(
            "task",
            not failures,
            "; ".join(failures) or f"new_tasks={len(new_tasks)}",
        )

    if expect.status_any and task.status not in expect.status_any:
        failures.append(
            f"status={task.status.value} 期望之一 "
            f"{[s.value for s in expect.status_any]}"
        )
    for fragment in expect.title_contains:
        if fragment not in (task.title or ""):
            failures.append(f"title 不含 {fragment!r}")
    for fragment in expect.goal_contains:
        if fragment not in (task.goal or ""):
            failures.append(f"goal 不含 {fragment!r}")
    task_content = "\n".join(
        (task.title, task.goal or "", *(step.title for step in task.steps))
    )
    for fragment in expect.content_contains:
        if fragment not in task_content:
            failures.append(f"Task 标题/目标/步骤均不含 {fragment!r}")
    if expect.min_steps is not None and len(task.steps) < expect.min_steps:
        failures.append(f"steps={len(task.steps)} 少于 {expect.min_steps}")
    for step_expect in expect.steps:
        failures.extend(
            _check_step(task, step_expect)
        )
    if expect.all_steps_done is not None:
        actual_all_done = bool(task.steps) and all(
            step.status is TaskStepStatus.DONE for step in task.steps
        )
        if actual_all_done != expect.all_steps_done:
            failures.append(
                f"all_steps_done={actual_all_done} 期望 {expect.all_steps_done}"
            )
    identity = (
        f"task={task.id[:8]} title={task.title!r} status={task.status.value}"
    )
    detail = f"{identity}; {'; '.join(failures)}" if failures else identity
    return CheckResult("task", not failures, detail)


def _check_step(task: Task, step_expect: Any) -> list[str]:
    failures: list[str] = []
    step = next((s for s in task.steps if s.id == step_expect.id), None)
    if step is None:
        failures.append(f"步骤 {step_expect.id} 不存在")
        return failures
    if step_expect.status is not None and step.status is not step_expect.status:
        failures.append(
            f"步骤 {step_expect.id} status={step.status.value} "
            f"期望 {step_expect.status.value}"
        )
    if step_expect.status_any and step.status not in step_expect.status_any:
        failures.append(
            f"步骤 {step_expect.id} status={step.status.value} 期望之一 "
            f"{[s.value for s in step_expect.status_any]}"
        )
    if step_expect.note_required and not (step.note or "").strip():
        failures.append(f"步骤 {step_expect.id} 缺少 note")
    return failures


def _check_files(scenario: Scenario, workspace: Path) -> CheckResult:
    if not scenario.expect.files:
        return CheckResult("files", True, "未声明文件期望", applicable=False)
    failures: list[str] = []
    for file_expect in scenario.expect.files:
        path = (workspace / file_expect.path).resolve()
        try:
            path.relative_to(workspace.resolve())
        except ValueError:
            failures.append(f"文件期望路径越界：{file_expect.path}")
            continue
        exists = path.is_file()
        if exists != file_expect.exists:
            failures.append(
                f"{file_expect.path} exists={exists} 期望 {file_expect.exists}"
            )
            continue
        if exists:
            content = path.read_text(encoding="utf-8")
            for fragment in file_expect.contains:
                if fragment not in content:
                    failures.append(f"{file_expect.path} 不含 {fragment!r}")
    return CheckResult("files", not failures, "; ".join(failures) or "ok")


def _check_answer(scenario: Scenario, result: AgentResult) -> CheckResult:
    keypoints = scenario.expect.answer.keypoints
    any_of = scenario.expect.answer.any_of
    if not keypoints and not any_of:
        return CheckResult("answer", True, "无关键点要求", applicable=False)
    content = result.final_message.content or ""
    missing = [kp for kp in keypoints if kp not in content]
    any_hit = [kp for kp in any_of if kp in content]
    failures: list[str] = []
    if missing:
        failures.append(f"missing_keypoints={missing}")
    if any_of and not any_hit:
        failures.append(f"no_any_of_hit={list(any_of)}")
    if failures:
        failures.append(f"answer={content[:300]!r}")
    return CheckResult("answer", not failures, "; ".join(failures) or "ok")


def _check_compaction(scenario: Scenario, events: list[AgentEvent]) -> CheckResult:
    if not scenario.expect.requires_compaction:
        return CheckResult("compaction", True, "未要求压缩", applicable=False)
    compaction_events = [
        event
        for event in events
        if event.type is AgentEventType.MODEL_STARTED
        and bool(event.requires_compaction)
    ]
    compacted = any(
        event.type is AgentEventType.MODEL_STARTED
        and bool(event.requires_compaction)
        and event.compaction_stage not in (None, "none")
        and bool(event.context_trimmed)
        for event in events
    )
    details = [
        {
            "stage": event.compaction_stage,
            "trimmed": event.context_trimmed,
            "before": event.original_estimated_input_tokens,
            "after": event.prepared_input_tokens,
            "input_budget": event.input_budget,
            "trigger": event.trigger_tokens,
            "target": event.target_tokens,
            "summary_updated": event.summary_updated,
            "summary_error": event.summary_error,
        }
        for event in compaction_events
    ]
    return CheckResult(
        "compaction",
        compacted,
        f"compaction_events={details}",
    )


__all__ = ["CheckResult", "_CHECK_NAMES", "run_checks"]
