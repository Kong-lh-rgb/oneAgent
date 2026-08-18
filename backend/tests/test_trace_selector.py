"""TaskTraceSelector 的筛选测试。

验证：Skill Learning 拿到的是"完成这个 Task 的实际执行过程"
（task_update 锚点之间的 Agent Step 区间），而不是整个 Run，
也不是只有 task_update 本身。
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.agent.events import AgentEvent, AgentEventType
from app.models.types import ToolCall, ToolResult
from app.skill_learning.trace_selector import TaskTraceSelector
from app.task import Task

TASK_A = "a" * 32
TASK_B = "b" * 32


def _task(task_id: str = TASK_A, run_ids: tuple[str, ...] = ("r1",)) -> Task:
    return Task(
        id=task_id,
        title="测试任务",
        owner_conversation_id="conv",
        run_ids=run_ids,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _tool_event(
    run_id: str,
    sequence: int,
    step: int,
    name: str,
    arguments: dict,
    *,
    success: bool = True,
) -> AgentEvent:
    return AgentEvent(
        run_id=run_id,
        sequence=sequence,
        step=step,
        type=AgentEventType.TOOL_COMPLETED,
        tool_call=ToolCall(id=f"c{sequence}", name=name, arguments=arguments),
        tool_result=ToolResult(
            tool_call_id=f"c{sequence}",
            tool_name=name,
            success=success,
            duration_ms=0.0,
        ),
    )


def _run(*events: AgentEvent) -> tuple[AgentEvent, ...]:
    return events


def _selected_steps(
    selector: TaskTraceSelector,
    task: Task,
    run_events: dict[str, tuple[AgentEvent, ...]],
    *,
    max_events: int | None = None,
) -> list[int]:
    return [
        event.step
        for event in selector.select(task, run_events, max_events=max_events)
        if event.step is not None
    ]


# ---------------------------------------------------------------------------
# 1. Same-run 精确区间
# ---------------------------------------------------------------------------


def test_same_run_exact_range() -> None:
    run = _run(
        _tool_event("r1", 1, 1, "read_file", {}),  # unrelated
        _tool_event(
            "r1", 2, 2, "task_update",
            {"task_id": TASK_A, "step_id": "s1", "step_status": "in_progress"},
        ),
        _tool_event("r1", 3, 3, "run_pytest", {}, success=False),
        _tool_event("r1", 4, 4, "edit_file", {}),
        _tool_event("r1", 5, 5, "run_pytest", {}),
        _tool_event(
            "r1", 6, 6, "task_update",
            {"task_id": TASK_A, "step_id": "s1", "step_status": "done",
             "step_note": "ok"},
        ),
        _tool_event("r1", 7, 7, "read_file", {}),  # unrelated
    )
    selector = TaskTraceSelector()
    assert _selected_steps(selector, _task(), {"r1": run}) == [2, 3, 4, 5, 6]


# ---------------------------------------------------------------------------
# 2. 只取当前 Task
# ---------------------------------------------------------------------------


def test_only_current_task_anchors() -> None:
    run = _run(
        _tool_event("r1", 1, 1, "read_file", {}),
        _tool_event(
            "r1", 2, 2, "task_update",
            {"task_id": TASK_B, "step_id": "s1", "step_status": "in_progress"},
        ),  # Task B 的 Anchor：不得扩大 Task A 的范围
        _tool_event("r1", 3, 3, "read_file", {}),
        _tool_event(
            "r1", 4, 4, "task_update",
            {"task_id": TASK_A, "step_id": "s1", "step_status": "in_progress"},
        ),
        _tool_event("r1", 5, 5, "edit_file", {}),
        _tool_event("r1", 6, 6, "run_pytest", {}),
        _tool_event(
            "r1", 7, 7, "task_update",
            {"task_id": TASK_A, "step_id": "s1", "step_status": "done",
             "step_note": "ok"},
        ),
    )
    selector = TaskTraceSelector()
    assert _selected_steps(selector, _task(TASK_A), {"r1": run}) == [4, 5, 6, 7]


# ---------------------------------------------------------------------------
# 3. failed task_update 不能作为 Anchor
# ---------------------------------------------------------------------------


def test_failed_task_update_is_not_anchor() -> None:
    run = _run(
        _tool_event(
            "r1", 1, 1, "task_update",
            {"task_id": TASK_A, "step_id": "s1", "step_status": "in_progress"},
            success=False,
        ),
        _tool_event("r1", 2, 2, "edit_file", {}),
    )
    selector = TaskTraceSelector()
    # 唯一的 task_update 失败 → 无有效 Anchor → 空（Task-only fallback）。
    assert selector.select(_task(), {"r1": run}) == ()


# ---------------------------------------------------------------------------
# 4. 缺 in_progress → bounded backward window
# ---------------------------------------------------------------------------


def test_missing_in_progress_uses_backward_window() -> None:
    run = _run(
        _tool_event("r1", 1, 1, "read_file", {}),
        _tool_event("r1", 2, 2, "run_pytest", {}, success=False),
        _tool_event("r1", 3, 3, "edit_file", {}),
        _tool_event(
            "r1", 4, 4, "task_update",
            {"task_id": TASK_A, "step_id": "s1", "step_status": "done",
             "step_note": "ok"},
        ),
    )
    selector = TaskTraceSelector(backward_window_steps=5)
    # 不能只剩 step 4：backward window 覆盖 [1, 4]。
    assert _selected_steps(selector, _task(), {"r1": run}) == [1, 2, 3, 4]


# ---------------------------------------------------------------------------
# 5. 跨 Run 的 TaskStep
# ---------------------------------------------------------------------------


def test_cross_run_step_span() -> None:
    task = _task(run_ids=("r1", "r4"))
    run1 = _run(
        _tool_event(
            "r1", 1, 5, "task_update",
            {"task_id": TASK_A, "step_id": "s1", "step_status": "in_progress"},
        ),
        _tool_event("r1", 2, 6, "read_file", {}),
        _tool_event("r1", 3, 7, "run_pytest", {}, success=False),
    )
    run4 = _run(
        _tool_event("r4", 1, 1, "read_file", {}),
        _tool_event("r4", 2, 2, "edit_file", {}),
        _tool_event("r4", 3, 3, "run_pytest", {}),
        _tool_event(
            "r4", 4, 4, "task_update",
            {"task_id": TASK_A, "step_id": "s1", "step_status": "done",
             "step_note": "ok"},
        ),
    )
    selector = TaskTraceSelector()
    # run-1 从 in_progress(5) 到结束(7)；run-4 从开始(1) 到 done(4)。
    assert _selected_steps(
        selector, task, {"r1": run1, "r4": run4}
    ) == [5, 6, 7, 1, 2, 3, 4]


# ---------------------------------------------------------------------------
# 6. Run 中前后都有无关工作
# ---------------------------------------------------------------------------


def test_unrelated_before_and_after_excluded() -> None:
    run = _run(
        _tool_event("r1", 1, 1, "read_file", {}),  # span 前无关
        _tool_event(
            "r1", 2, 2, "task_update",
            {"task_id": TASK_A, "step_id": "s1", "step_status": "in_progress"},
        ),
        _tool_event("r1", 3, 3, "edit_file", {}),
        _tool_event(
            "r1", 4, 4, "task_update",
            {"task_id": TASK_A, "step_id": "s1", "step_status": "done",
             "step_note": "ok"},
        ),
        _tool_event("r1", 5, 5, "read_file", {}),  # span 后无关
    )
    selector = TaskTraceSelector()
    assert _selected_steps(selector, _task(), {"r1": run}) == [2, 3, 4]


# ---------------------------------------------------------------------------
# 7. 普通 goal/state/status update（无 step_id）→ nearby window
# ---------------------------------------------------------------------------


def test_plain_update_uses_nearby_window() -> None:
    run = _run(
        _tool_event("r1", 1, 1, "read_file", {}),
        _tool_event("r1", 2, 2, "task_update", {"task_id": TASK_A, "goal": "新目标"}),
        _tool_event("r1", 3, 3, "run_pytest", {}),
    )
    selector = TaskTraceSelector(backward_window_steps=5)
    # 无 step_id：anchor 附近 bounded window（前 N 步 + 锚点步），不丢弃。
    assert _selected_steps(selector, _task(), {"r1": run}) == [1, 2]


# ---------------------------------------------------------------------------
# 8. 无有效 Anchor → 空（Task-only fallback）
# ---------------------------------------------------------------------------


def test_no_valid_anchor_returns_empty() -> None:
    run = _run(
        _tool_event("r1", 1, 1, "read_file", {}),
        _tool_event("r1", 2, 2, "run_pytest", {}),
    )
    selector = TaskTraceSelector()
    assert selector.select(_task(), {"r1": run}) == ()


def test_missing_run_returns_empty() -> None:
    selector = TaskTraceSelector()
    assert selector.select(_task(), {}) == ()


# ---------------------------------------------------------------------------
# 9. max_events 是硬上限
# ---------------------------------------------------------------------------


def test_max_events_is_hard_limit() -> None:
    events: list[AgentEvent] = []
    for step in range(1, 21):
        if step == 1:
            events.append(
                _tool_event(
                    "r1", step, step, "task_update",
                    {"task_id": TASK_A, "step_id": "s1",
                     "step_status": "in_progress"},
                )
            )
        elif step == 20:
            events.append(
                _tool_event(
                    "r1", step, step, "task_update",
                    {"task_id": TASK_A, "step_id": "s1",
                     "step_status": "done", "step_note": "ok"},
                )
            )
        else:
            events.append(_tool_event("r1", step, step, "run_pytest", {}))
    selector = TaskTraceSelector()
    selected = selector.select(_task(), {"r1": tuple(events)}, max_events=5)
    # 严格 <= 5，且取执行顺序前 5 个。
    assert len(selected) == 5
    assert [event.step for event in selected] == [1, 2, 3, 4, 5]


# ---------------------------------------------------------------------------
# 10. 同一 Event 被多个 Range 覆盖 → 只保留一次，顺序不变
# ---------------------------------------------------------------------------


def test_overlapping_ranges_deduplicate() -> None:
    run = _run(
        _tool_event("r1", 1, 1, "read_file", {}),
        _tool_event(
            "r1", 2, 2, "task_update",
            {"task_id": TASK_A, "step_id": "s1", "step_status": "in_progress"},
        ),
        _tool_event(
            "r1", 3, 3, "task_update",
            {"task_id": TASK_A, "step_id": "s2", "step_status": "in_progress"},
        ),  # step 3 同时被 s1 与 s2 区间覆盖
        _tool_event("r1", 4, 4, "edit_file", {}),
        _tool_event(
            "r1", 5, 5, "task_update",
            {"task_id": TASK_A, "step_id": "s2", "step_status": "done",
             "step_note": "a"},
        ),
        _tool_event(
            "r1", 6, 6, "task_update",
            {"task_id": TASK_A, "step_id": "s1", "step_status": "done",
             "step_note": "b"},
        ),
    )
    selector = TaskTraceSelector()
    selected = _selected_steps(selector, _task(), {"r1": run})
    # s1:[2,6] ∪ s2:[3,5] = {2,3,4,5,6}，无重复、顺序不变。
    assert selected == [2, 3, 4, 5, 6]
    assert len(selected) == len(set(selected))


# ---------------------------------------------------------------------------
# 11. 重复 in_progress：保留最早的 start anchor，不因续跑 in_progress 丢失早期 Trace
# ---------------------------------------------------------------------------


def test_repeated_in_progress_keeps_earliest_segment() -> None:
    """同一 TaskStep 在多个 Run 重复收到 in_progress → 保留最早那段执行。"""
    task = _task(run_ids=("r1", "r2", "r3"))
    run1 = _run(
        _tool_event(
            "r1", 1, 2, "task_update",
            {"task_id": TASK_A, "step_id": "s1", "step_status": "in_progress"},
        ),  # 最早的 start anchor
        _tool_event("r1", 2, 3, "read_file", {}),
    )
    run2 = _run(
        _tool_event("r2", 1, 1, "read_file", {}),
        _tool_event(
            "r2", 2, 2, "task_update",
            {"task_id": TASK_A, "step_id": "s1", "step_status": "in_progress"},
        ),  # 重复 in_progress → continuation，不重置
        _tool_event("r2", 3, 3, "run_pytest", {}, success=False),
    )
    run3 = _run(
        _tool_event("r3", 1, 1, "read_file", {}),
        _tool_event("r3", 2, 2, "edit_file", {}),
        _tool_event("r3", 3, 3, "run_pytest", {}),
        _tool_event(
            "r3", 4, 4, "task_update",
            {"task_id": TASK_A, "step_id": "s1", "step_status": "done",
             "step_note": "ok"},
        ),
    )
    selector = TaskTraceSelector()
    # run-1（最早 in_progress）必须包含：r1[2,3] + r2[1,2,3] + r3[1,2,3,4]。
    assert _selected_steps(
        selector, task, {"r1": run1, "r2": run2, "r3": run3}
    ) == [2, 3, 1, 2, 3, 1, 2, 3, 4]
