"""Run Checkpoint 持久化与恢复状态机测试。"""

from __future__ import annotations

import pytest

from app.agent.result import AgentStopReason
from app.checkpoint import (
    CheckpointPhase,
    CheckpointStatus,
    SQLiteCheckpointStore,
)
from app.models.types import Message, MessageRole, ToolCall, ToolResult

_USER_MESSAGE = Message(role=MessageRole.USER, content="继续任务")


@pytest.fixture
async def store(tmp_path) -> SQLiteCheckpointStore:
    instance = SQLiteCheckpointStore(tmp_path / "vesta.db")
    await instance.initialize()
    return instance


async def test_checkpoint_completed_lifecycle(
    store: SQLiteCheckpointStore,
) -> None:
    started = await store.start(
        "run-1",
        conversation_id="conv-1",
        user_message=_USER_MESSAGE,
    )
    assert started.status is CheckpointStatus.RUNNING
    assert started.phase is CheckpointPhase.STARTING

    requesting = await store.before_model("run-1", step=1)
    assert requesting.phase is CheckpointPhase.MODEL_REQUEST
    completed = await store.complete(
        "run-1",
        stop_reason=AgentStopReason.FINAL_ANSWER,
    )

    assert completed.status is CheckpointStatus.COMPLETED
    assert completed.phase is CheckpointPhase.FINISHED
    assert completed.completed_at is not None
    assert completed.revision == 3


async def test_checkpoint_preserves_pending_and_completed_tools_after_interrupt(
    store: SQLiteCheckpointStore,
) -> None:
    first = ToolCall(id="call-1", name="write_file", arguments={"path": "a"})
    second = ToolCall(id="call-2", name="send_email", arguments={"to": "a"})
    await store.start(
        "run-tools",
        conversation_id="conv-1",
        user_message=_USER_MESSAGE,
    )
    await store.before_model("run-tools", step=1)
    await store.before_tools("run-tools", step=1, tool_calls=(first, second))
    first_result = ToolResult(
        tool_call_id=first.id,
        tool_name=first.name,
        success=True,
        output="written",
        duration_ms=2,
    )
    await store.complete_tool("run-tools", first_result)
    interrupted = await store.interrupt(
        "run-tools",
        error="CancelledError",
    )

    reopened = SQLiteCheckpointStore(store.database_path)
    await reopened.initialize()
    loaded = await reopened.get(interrupted.run_id)

    assert loaded is not None
    assert loaded.status is CheckpointStatus.INTERRUPTED
    assert loaded.phase is CheckpointPhase.TOOL_EXECUTION
    assert loaded.pending_tool_calls == (second,)
    assert loaded.completed_tool_results == (first_result,)


async def test_startup_recovers_stale_running_checkpoint(
    store: SQLiteCheckpointStore,
) -> None:
    call = ToolCall(id="uncertain", name="write_file", arguments={})
    await store.start(
        "stale-run",
        conversation_id="conv-1",
        user_message=_USER_MESSAGE,
    )
    await store.before_model("stale-run", step=2)
    await store.before_tools("stale-run", step=2, tool_calls=(call,))
    await store.start(
        "other-run",
        conversation_id="conv-2",
        user_message=_USER_MESSAGE,
    )

    recovered = await store.recover_running(conversation_id="conv-1")

    assert [item.run_id for item in recovered] == ["stale-run"]
    assert recovered[0].status is CheckpointStatus.INTERRUPTED
    assert recovered[0].pending_tool_calls == (call,)
    other = await store.get("other-run")
    assert other is not None and other.status is CheckpointStatus.RUNNING


async def test_interrupted_checkpoint_is_recovered_only_after_explicit_mark(
    store: SQLiteCheckpointStore,
) -> None:
    await store.start(
        "old-run",
        conversation_id="conv-1",
        user_message=_USER_MESSAGE,
    )
    await store.interrupt("old-run", error="process stopped")

    latest = await store.latest_unrecovered("conv-1")
    assert latest is not None and latest.run_id == "old-run"

    marked = await store.mark_recovered(
        "old-run",
        recovered_by_run_id="new-run",
    )
    assert marked.recovered_by_run_id == "new-run"
    assert await store.latest_unrecovered("conv-1") is None


async def test_failed_checkpoint_keeps_stop_reason(
    store: SQLiteCheckpointStore,
) -> None:
    await store.start(
        "failed-run",
        conversation_id="conv-1",
        user_message=_USER_MESSAGE,
    )
    await store.before_model("failed-run", step=1)
    failed = await store.fail(
        "failed-run",
        stop_reason=AgentStopReason.MODEL_ERROR,
        error="connection failed",
    )

    assert failed.status is CheckpointStatus.FAILED
    assert failed.stop_reason is AgentStopReason.MODEL_ERROR
    assert failed.error == "connection failed"


async def test_checkpoint_cannot_skip_unresolved_tool_calls(
    store: SQLiteCheckpointStore,
) -> None:
    call = ToolCall(id="pending", name="write_file", arguments={})
    await store.start(
        "run-pending",
        conversation_id="conv-1",
        user_message=_USER_MESSAGE,
    )
    await store.before_tools("run-pending", step=1, tool_calls=(call,))

    with pytest.raises(ValueError, match="tool calls are pending"):
        await store.before_model("run-pending", step=2)
    with pytest.raises(ValueError, match="pending tool calls"):
        await store.complete(
            "run-pending",
            stop_reason=AgentStopReason.FINAL_ANSWER,
        )

    unchanged = await store.get("run-pending")
    assert unchanged is not None
    assert unchanged.pending_tool_calls == (call,)
