"""Computer Target & Recovery V1 的离线停滞保护测试。"""

from __future__ import annotations

import json

from app.agent.computer_guard import ComputerStagnationGuard
from app.models.types import ToolCall, ToolResult


def _call(call_id: str, name: str = "computer_observe") -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments={"attempt": call_id})


def _failure(call: ToolCall, code: str = "stale_observation") -> ToolResult:
    return ToolResult(
        tool_call_id=call.id,
        tool_name=call.name,
        success=False,
        error=f"ComputerHelperError: {code}",
        duration_ms=1,
    )


def _observation(call: ToolCall, value: str, *, x: int = 0) -> ToolResult:
    return ToolResult(
        tool_call_id=call.id,
        tool_name=call.name,
        success=True,
        output=json.dumps(
            {
                "target": {"pid": 42, "name": "Notes"},
                "active_window": {
                    "ref": "w1",
                    "title": "Notes",
                    "bounds": {"x": x, "y": 0, "width": 800, "height": 600},
                },
                "elements": [
                    {
                        "ref": "e1",
                        "role": "text_area",
                        "value": value,
                        "bounds": {"x": x, "y": 20, "width": 400, "height": 300},
                    }
                ],
            }
        ),
        duration_ms=1,
    )


def test_guard_warns_then_halts_same_failure_without_progress() -> None:
    guard = ComputerStagnationGuard()

    first = guard.record(_call("1"), _failure(_call("1")))
    second = guard.record(_call("2"), _failure(_call("2")))
    third = guard.record(_call("3"), _failure(_call("3")))

    assert first.feedback is None and first.halt is False
    assert second.feedback is not None and second.halt is False
    assert third.feedback is not None and third.halt is True


def test_new_desktop_evidence_breaks_failure_streak() -> None:
    guard = ComputerStagnationGuard()
    first = _call("1", "computer_click")
    second = _call("2", "computer_key")
    observe = _call("3")

    guard.record(first, _failure(first))
    guard.record(second, _failure(second))
    guard.record(observe, _observation(observe, "new state"))
    after_progress = _call("4", "computer_type")
    decision = guard.record(after_progress, _failure(after_progress))

    assert decision.feedback is None
    assert decision.halt is False


def test_non_computer_failures_are_ignored() -> None:
    guard = ComputerStagnationGuard()
    call = _call("1", "write_file")

    for _ in range(5):
        decision = guard.record(call, _failure(call))

    assert decision.feedback is None
    assert decision.halt is False


def test_window_coordinate_move_is_not_meaningful_desktop_progress() -> None:
    guard = ComputerStagnationGuard()
    observe_one = _call("observe-1")
    observe_two = _call("observe-2")
    first_failure = _call("failure-1", "computer_key")
    second_failure = _call("failure-2", "computer_click")

    guard.record(observe_one, _observation(observe_one, "same", x=100))
    guard.record(first_failure, _failure(first_failure))
    guard.record(observe_two, _observation(observe_two, "same", x=500))
    decision = guard.record(second_failure, _failure(second_failure))

    assert decision.feedback is not None
    assert decision.halt is False
