from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.agent.events import AgentEvent, AgentEventType
from app.agent.result import AgentError, AgentResult, AgentStopReason
from app.models.types import Message, MessageRole, ModelUsage, ToolCall, ToolResult


def test_event_has_unique_id_and_utc_event_time() -> None:
    first = AgentEvent(run_id="run-1", type=AgentEventType.AGENT_STARTED)
    second = AgentEvent(run_id="run-1", type=AgentEventType.MODEL_STARTED)

    assert first.event_id != second.event_id
    assert first.event_time.tzinfo is UTC
    assert second.event_time >= first.event_time
    assert first.model_dump(mode="json")["event_time"].endswith("Z")


def test_event_normalizes_aware_time_to_utc() -> None:
    china_timezone = timezone(timedelta(hours=8))
    event = AgentEvent(
        run_id="run-1",
        type=AgentEventType.AGENT_STARTED,
        event_time=datetime(2026, 8, 4, 12, 0, tzinfo=china_timezone),
    )

    assert event.event_time == datetime(2026, 8, 4, 4, 0, tzinfo=UTC)


def test_event_reuses_existing_payload_models() -> None:
    tool_call = ToolCall(
        id="call-1",
        name="read_file",
        arguments={"path": "hello.txt"},
    )
    tool_result = ToolResult(
        tool_call_id="call-1",
        tool_name="read_file",
        success=True,
        output="你好",
        duration_ms=1.5,
    )
    event = AgentEvent(
        run_id="run-1",
        conversation_id="conversation-1",
        sequence=4,
        type=AgentEventType.TOOL_COMPLETED,
        step=2,
        message=Message(role=MessageRole.ASSISTANT, tool_calls=(tool_call,)),
        tool_call=tool_call,
        tool_result=tool_result,
        usage=ModelUsage(input_tokens=10, output_tokens=2, total_tokens=12),
        stop_reason=AgentStopReason.FINAL_ANSWER,
        error=AgentError(type="ExampleError", message="示例"),
    )

    restored = AgentEvent.model_validate_json(event.model_dump_json())

    assert restored == event
    assert restored.tool_call == tool_call
    assert restored.tool_result == tool_result
    assert restored.conversation_id == "conversation-1"


def test_completed_event_can_carry_final_agent_result() -> None:
    final_message = Message(role=MessageRole.ASSISTANT, content="完成")
    result = AgentResult(
        run_id="run-1",
        final_message=final_message,
        messages=(final_message,),
        steps=1,
        stop_reason=AgentStopReason.FINAL_ANSWER,
    )
    event = AgentEvent(
        run_id=result.run_id,
        type=AgentEventType.AGENT_COMPLETED,
        result=result,
    )

    restored = AgentEvent.model_validate_json(event.model_dump_json())

    assert restored.result == result


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_id", "  "),
        ("sequence", -1),
        ("step", 0),
        ("event_time", datetime(2026, 8, 4, 12, 0)),
    ],
)
def test_event_rejects_invalid_identity_order_or_time(field, value) -> None:
    values = {
        "run_id": "run-1",
        "type": AgentEventType.AGENT_STARTED,
        field: value,
    }

    with pytest.raises(ValidationError):
        AgentEvent.model_validate(values)
