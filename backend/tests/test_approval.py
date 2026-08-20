"""Async Approval V1 单元测试。

覆盖：创建 ApprovalRequest / approve / deny / 重复 resolve 被拒绝 /
resolved 不可再修改 / DesktopApprovalGate 等待与唤醒 / 断线（无人决定）不自动
approve·deny / Agent 等待审批后继续运行 / 审批结果进入 AgentEvent。

用 fake model / fake tool，不调用真实模型 API。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest
from pydantic import SecretStr

from app.agent.events import AgentEventType, InMemoryEventHandler
from app.agent.runtime import AgentRuntime
from app.approval import (
    ApprovalRequestStatus,
    DesktopApprovalGate,
    SQLiteApprovalStore,
)
from app.models.adapter import ModelAdapter
from app.models.config import ModelSettings, ProviderConfig
from app.models.registry import ModelAdapterRegistry
from app.models.types import (
    ApiStyle,
    Message,
    MessageRole,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    ToolCall,
    ToolDefinition,
    ToolPermission,
)
from app.tools.approval import (
    ApprovalDecision,
)
from app.tools.approval import (
    ApprovalRequest as ApprovalSubmission,
)
from app.tools.base import BaseTool
from app.tools.registry import ToolRegistry


@pytest.fixture
async def approval_store(tmp_path):
    store = SQLiteApprovalStore(tmp_path / "vesta.db")
    await store.initialize()
    return store


async def _create(store, **overrides) -> object:
    params = {
        "run_id": "run-1",
        "conversation_id": "conv-1",
        "tool_name": "run_shell_command",
        "tool_call_id": "call-1",
        "arguments": {"command": "pytest"},
        "reason": "运行测试",
    }
    params.update(overrides)
    return await store.create(**params)


# ---------------------------------------------------------------------------
# store：创建 / approve / deny / 重复 resolve
# ---------------------------------------------------------------------------


async def test_create_approval_request(approval_store) -> None:
    record = await _create(approval_store)

    assert record.id
    assert record.status is ApprovalRequestStatus.PENDING
    assert record.resolved_at is None
    assert record.tool_name == "run_shell_command"
    assert record.tool_call_id == "call-1"
    assert record.arguments == {"command": "pytest"}
    assert record.reason == "运行测试"
    assert record.run_id == "run-1"
    assert record.conversation_id == "conv-1"

    fetched = await approval_store.get(record.id)
    assert fetched == record


async def test_approve(approval_store) -> None:
    record = await _create(approval_store)

    resolved = await approval_store.resolve(
        record.id,
        ApprovalRequestStatus.APPROVED,
    )
    assert resolved.status is ApprovalRequestStatus.APPROVED
    assert resolved.resolved_at is not None
    assert resolved.created_at is not None


async def test_deny(approval_store) -> None:
    record = await _create(approval_store)

    resolved = await approval_store.resolve(
        record.id,
        ApprovalRequestStatus.DENIED,
    )
    assert resolved.status is ApprovalRequestStatus.DENIED
    assert resolved.resolved_at is not None


async def test_duplicate_resolve_rejected(approval_store) -> None:
    record = await _create(approval_store)
    await approval_store.resolve(record.id, ApprovalRequestStatus.APPROVED)

    # 已 resolved 的 approval 不能再次修改：approve / deny 都拒绝。
    with pytest.raises(ValueError, match="already resolved"):
        await approval_store.resolve(record.id, ApprovalRequestStatus.DENIED)
    with pytest.raises(ValueError, match="already resolved"):
        await approval_store.resolve(record.id, ApprovalRequestStatus.APPROVED)

    # 持久化的事实不变。
    fetched = await approval_store.get(record.id)
    assert fetched.status is ApprovalRequestStatus.APPROVED


async def test_resolve_to_pending_rejected(approval_store) -> None:
    record = await _create(approval_store)
    with pytest.raises(ValueError, match="PENDING"):
        await approval_store.resolve(record.id, ApprovalRequestStatus.PENDING)


async def test_list_filters(approval_store) -> None:
    first = await _create(approval_store, run_id="run-1")
    second = await _create(
        approval_store,
        run_id="run-2",
        tool_name="http_request",
        tool_call_id="call-2",
    )
    await approval_store.resolve(first.id, ApprovalRequestStatus.APPROVED)

    pending = await approval_store.list(status=ApprovalRequestStatus.PENDING)
    assert [item.id for item in pending] == [second.id]

    all_records = await approval_store.list()
    assert {item.id for item in all_records} == {first.id, second.id}

    by_run = await approval_store.list(run_id="run-1")
    assert [item.id for item in by_run] == [first.id]


async def test_cancelled_approval_cannot_resolve(approval_store) -> None:
    """CANCELLED 是终态：不能再 approve / deny。"""

    record = await _create(approval_store, run_id="run-1")
    resolved = await approval_store.resolve(
        record.id,
        ApprovalRequestStatus.CANCELLED,
    )
    assert resolved.status is ApprovalRequestStatus.CANCELLED

    with pytest.raises(ValueError, match="already resolved"):
        await approval_store.resolve(record.id, ApprovalRequestStatus.APPROVED)
    with pytest.raises(ValueError, match="already resolved"):
        await approval_store.resolve(record.id, ApprovalRequestStatus.DENIED)


async def test_cancel_pending_for_run(approval_store) -> None:
    """Run cancel 时该 run 下仍 PENDING 的 approval 自动变为 CANCELLED。"""

    target = await _create(approval_store, run_id="run-cancel", tool_call_id="c1")
    other_run = await _create(
        approval_store,
        run_id="run-other",
        tool_call_id="c2",
    )
    resolved = await _create(approval_store, run_id="run-cancel", tool_call_id="c3")
    await approval_store.resolve(resolved.id, ApprovalRequestStatus.APPROVED)

    cancelled = await approval_store.cancel_pending_for_run("run-cancel")

    assert cancelled == 1  # 只取消 PENDING 的，不动已 resolved
    assert (await approval_store.get(target.id)).status is (
        ApprovalRequestStatus.CANCELLED
    )
    assert (await approval_store.get(other_run.id)).status is (
        ApprovalRequestStatus.PENDING
    )
    assert (await approval_store.get(resolved.id)).status is (
        ApprovalRequestStatus.APPROVED
    )


async def test_reconcile_orphans_cancels_orphan_pending(approval_store) -> None:
    """Host 启动 reconcile：没有对应活跃 Run 的 PENDING approval → CANCELLED。"""

    live = await _create(approval_store, run_id="active-run", tool_call_id="c1")
    orphan = await _create(approval_store, run_id="dead-run", tool_call_id="c2")
    no_run = await _create(approval_store, run_id=None, tool_call_id="c3")
    resolved = await _create(approval_store, run_id="dead-run", tool_call_id="c4")
    await approval_store.resolve(resolved.id, ApprovalRequestStatus.APPROVED)

    cancelled = await approval_store.reconcile_orphans({"active-run"})

    assert cancelled == 2  # orphan + no_run
    assert (await approval_store.get(live.id)).status is (
        ApprovalRequestStatus.PENDING
    )
    assert (await approval_store.get(orphan.id)).status is (
        ApprovalRequestStatus.CANCELLED
    )
    assert (await approval_store.get(no_run.id)).status is (
        ApprovalRequestStatus.CANCELLED
    )
    assert (await approval_store.get(resolved.id)).status is (
        ApprovalRequestStatus.APPROVED
    )


# ---------------------------------------------------------------------------
# DesktopApprovalGate：等待 / 唤醒 / 不自动决定
# ---------------------------------------------------------------------------


async def test_approve_right_after_record_created_wakes_run(approval_store) -> None:
    """竞态修复：记录出现后立刻 approve，等待中的 Run 一定被唤醒。

    回归场景：旧顺序 create→broadcast→register 存在 "数据库已 resolved 但
    Future 未注册 → Run 没被唤醒"。新顺序 create→register→broadcast 保证
    客户端看到记录时 Future 已注册（create 返回与 register 之间无 await）。
    """

    gate = DesktopApprovalGate(approval_store)
    submission = ApprovalSubmission(
        tool_call_id="call-1",
        tool_name="run_shell_command",
        arguments={"command": "pytest"},
        run_id="run-1",
    )
    waiting = asyncio.create_task(gate.request_approval(submission))

    # 轮询到记录出现（此时 Future 已注册）。
    for _ in range(500):
        pending = await approval_store.list(
            status=ApprovalRequestStatus.PENDING,
        )
        if pending:
            break
        await asyncio.sleep(0)
    assert pending, "应创建 PENDING ApprovalRequest"
    assert not waiting.done()

    # 立刻 approve（模拟 Desktop 看到记录后马上响应）。
    await gate.approve(pending[0].id)
    response = await asyncio.wait_for(waiting, timeout=5)
    assert response.decision is ApprovalDecision.APPROVED
    assert gate.pending_count == 0


async def test_cancel_run_cancels_pending_approval(tmp_path) -> None:
    """RunManager.cancel 会把该 run 下 PENDING 的 approval 置为 CANCELLED。"""

    from app.agent.runtime import AgentRuntime
    from app.checkpoint import SQLiteCheckpointStore
    from app.run import RunManager, SQLiteRunStore

    config = ProviderConfig(
        provider="fake",
        model="fake-model",
        api_key=SecretStr("offline-test-key"),
        api_style=ApiStyle.CHAT_COMPLETIONS,
    )
    adapter = _BlockingAdapter(config)
    registry = ModelAdapterRegistry(ModelSettings(_env_file=None))
    registry.register("fake", lambda _: adapter, config=config)

    database = tmp_path / "vesta.db"
    run_store = SQLiteRunStore(database)
    await run_store.initialize()
    checkpoint_store = SQLiteCheckpointStore(database)
    await checkpoint_store.initialize()
    approval_store = SQLiteApprovalStore(database)
    await approval_store.initialize()

    runtime = AgentRuntime(registry, ToolRegistry(), provider="fake")
    manager = RunManager(
        run_store,
        checkpoint_store,
        runtime,
        approval_store=approval_store,
    )
    run_id, _ = await manager.start("阻塞", conversation_id="conv-1")
    for _ in range(200):
        if adapter.started.is_set():
            break
        await asyncio.sleep(0)
    assert adapter.started.is_set()

    # 该 run 有一个 PENDING approval（模拟正在等待审批）。
    record = await approval_store.create(
        run_id=run_id,
        conversation_id="conv-1",
        tool_name="run_shell_command",
        tool_call_id="call-1",
        arguments={"command": "pytest"},
    )
    assert record.status is ApprovalRequestStatus.PENDING

    await manager.cancel(run_id)

    after = await approval_store.get(record.id)
    assert after is not None
    assert after.status is ApprovalRequestStatus.CANCELLED


class _BlockingAdapter(ModelAdapter):
    """阻塞在模型请求上，直到被取消（用于 cancel 测试）。"""

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self.started = asyncio.Event()
        self.cancelled = False
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        raise AssertionError("阻塞模型不应正常完成")

    async def close(self) -> None:
        pass


async def test_desktop_gate_waits_until_approve(approval_store) -> None:
    gate = DesktopApprovalGate(approval_store)
    notifications: list[tuple[str, object]] = []
    broadcasted = asyncio.Event()

    async def broadcaster(method: str, params: object) -> None:
        notifications.append((method, params))
        broadcasted.set()

    gate.set_broadcaster(broadcaster)

    submission = ApprovalSubmission(
        tool_call_id="call-1",
        tool_name="run_shell_command",
        arguments={"command": "pytest"},
        description="运行测试",
        run_id="run-1",
        conversation_id="conv-1",
    )
    waiting = asyncio.create_task(gate.request_approval(submission))

    # 显式等待 broadcaster 被调用（approval.required 已广播），而不是轮询数据库。
    # 数据库写入与 broadcast 之间可能发生 asyncio task switch：轮询 DB 会看到
    # PENDING 记录但 notifications 尚未写入，随后立即访问 notifications[0] 即竞态。
    # 用 Event 等待广播发生，消除对生产时序的依赖（不修改 DesktopApprovalGate）。
    await asyncio.wait_for(broadcasted.wait(), timeout=5)

    pending = await approval_store.list(status=ApprovalRequestStatus.PENDING)
    assert pending, "应创建 PENDING ApprovalRequest"
    assert notifications[0][0] == "approval.required"
    assert not waiting.done()

    approved = await gate.approve(pending[0].id)
    response = await asyncio.wait_for(waiting, timeout=5)

    assert response.decision is ApprovalDecision.APPROVED
    assert approved.status is ApprovalRequestStatus.APPROVED
    assert notifications[-1][0] == "approval.resolved"
    assert gate.pending_count == 0


async def test_desktop_gate_deny_returns_denied(approval_store) -> None:
    gate = DesktopApprovalGate(approval_store)
    submission = ApprovalSubmission(
        tool_call_id="call-1",
        tool_name="run_shell_command",
        arguments={"command": "pytest"},
        run_id="run-1",
    )
    waiting = asyncio.create_task(gate.request_approval(submission))

    for _ in range(200):
        pending = await approval_store.list(
            status=ApprovalRequestStatus.PENDING,
        )
        if pending:
            break
        await asyncio.sleep(0)

    await gate.deny(pending[0].id)
    response = await asyncio.wait_for(waiting, timeout=5)
    assert response.decision is ApprovalDecision.DENIED


async def test_gate_resolve_only_once(approval_store) -> None:
    """approve / deny 只能执行一次：第二次调用抛 ValueError。"""

    gate = DesktopApprovalGate(approval_store)
    submission = ApprovalSubmission(
        tool_call_id="call-1",
        tool_name="run_shell_command",
        arguments={"command": "pytest"},
        run_id="run-1",
    )
    waiting = asyncio.create_task(gate.request_approval(submission))
    for _ in range(200):
        pending = await approval_store.list(
            status=ApprovalRequestStatus.PENDING,
        )
        if pending:
            break
        await asyncio.sleep(0)
    approval_id = pending[0].id

    await gate.approve(approval_id)
    await asyncio.wait_for(waiting, timeout=5)
    with pytest.raises(ValueError, match="already resolved"):
        await gate.approve(approval_id)
    with pytest.raises(ValueError, match="already resolved"):
        await gate.deny(approval_id)


async def test_disconnect_does_not_auto_decide(approval_store) -> None:
    """断线（无人调用 approve / deny）不能自动决定：保持 PENDING。"""

    gate = DesktopApprovalGate(approval_store)
    submission = ApprovalSubmission(
        tool_call_id="call-1",
        tool_name="run_shell_command",
        arguments={"command": "pytest"},
        run_id="run-1",
    )
    waiting = asyncio.create_task(gate.request_approval(submission))
    for _ in range(200):
        pending = await approval_store.list(
            status=ApprovalRequestStatus.PENDING,
        )
        if pending:
            break
        await asyncio.sleep(0)

    assert pending and pending[0].status is ApprovalRequestStatus.PENDING
    # 等待任务仍在阻塞，没有被自动 approve / deny。
    assert not waiting.done()

    # 之后显式决定仍然有效。
    await gate.deny(pending[0].id)
    response = await asyncio.wait_for(waiting, timeout=5)
    assert response.decision is ApprovalDecision.DENIED


# ---------------------------------------------------------------------------
# Agent 等待审批后继续运行（runtime 级）
# ---------------------------------------------------------------------------


class ApprovalProbeTool(BaseTool):
    definition = ToolDefinition(
        name="approval_probe",
        description="需要审批的探测工具",
        parameters={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
        },
        permission=ToolPermission.HUMAN_APPROVAL,
    )

    def __init__(self) -> None:
        self.executions = 0

    async def execute(self, arguments: dict[str, object]) -> str:
        self.executions += 1
        return f"probe-{arguments.get('value')}"


class ScriptedAdapter(ModelAdapter):
    def __init__(
        self,
        config: ProviderConfig,
        responses: Sequence[ModelResponse | Exception],
    ) -> None:
        super().__init__(config)
        self.responses = list(responses)
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def close(self) -> None:
        pass


def _response(
    *,
    content: str | None = None,
    tool_calls: tuple[ToolCall, ...] = (),
) -> ModelResponse:
    return ModelResponse(
        id="fake-response",
        provider="fake",
        model="fake-model",
        message=Message(
            role=MessageRole.ASSISTANT,
            content=content,
            tool_calls=tool_calls,
        ),
        usage=ModelUsage(),
    )


def _registry(
    responses: Sequence[ModelResponse | Exception],
) -> tuple[ModelAdapterRegistry, ScriptedAdapter]:
    config = ProviderConfig(
        provider="fake",
        model="fake-model",
        api_key=SecretStr("offline-test-key"),
        api_style=ApiStyle.CHAT_COMPLETIONS,
    )
    adapter = ScriptedAdapter(config, responses)
    registry = ModelAdapterRegistry(ModelSettings(_env_file=None))
    registry.register("fake", lambda _: adapter, config=config)
    return registry, adapter


async def test_agent_waits_for_approval_then_continues(tmp_path) -> None:
    registry, _ = _registry(
        [
            _response(
                tool_calls=(
                    ToolCall(
                        id="ap-1",
                        name="approval_probe",
                        arguments={"value": 7},
                    ),
                )
            ),
            _response(content="审批通过，任务完成"),
        ]
    )
    tool = ApprovalProbeTool()
    tools = ToolRegistry()
    tools.register(tool)
    store = SQLiteApprovalStore(tmp_path / "vesta.db")
    await store.initialize()
    gate = DesktopApprovalGate(store)
    handler = InMemoryEventHandler()

    runtime = AgentRuntime(
        registry,
        tools,
        provider="fake",
        approval_gate=gate,
    )
    result_task = asyncio.create_task(
        runtime.run(
            "执行需要审批的任务",
            run_id="run-1",
            event_handler=handler,
        )
    )

    # 等到审批 PENDING。
    for _ in range(500):
        pending = await store.list(status=ApprovalRequestStatus.PENDING)
        if pending:
            break
        await asyncio.sleep(0)
    assert pending and pending[0].tool_name == "approval_probe"
    assert pending[0].run_id == "run-1"
    assert tool.executions == 0
    assert not result_task.done()

    # 用户批准 → Agent 继续执行 → 工具真正运行 → Run 完成。
    await gate.approve(pending[0].id)
    result = await asyncio.wait_for(result_task, timeout=10)

    assert result.ok is True
    assert tool.executions == 1
    assert result.tool_calls[0].result.success is True

    # 审批结果进入现有 AgentEvent。
    approval_events = [
        event
        for event in handler.events
        if event.type
        in {
            AgentEventType.TOOL_APPROVAL_REQUIRED,
            AgentEventType.TOOL_APPROVAL_COMPLETED,
        }
    ]
    assert [event.type for event in approval_events] == [
        AgentEventType.TOOL_APPROVAL_REQUIRED,
        AgentEventType.TOOL_APPROVAL_COMPLETED,
    ]
    assert approval_events[1].approval_decision is ApprovalDecision.APPROVED


async def test_agent_denied_tool_not_executed(tmp_path) -> None:
    registry, _ = _registry(
        [
            _response(
                tool_calls=(
                    ToolCall(
                        id="ap-1",
                        name="approval_probe",
                        arguments={"value": 1},
                    ),
                )
            ),
            _response(content="审批被拒绝"),
        ]
    )
    tool = ApprovalProbeTool()
    tools = ToolRegistry()
    tools.register(tool)
    store = SQLiteApprovalStore(tmp_path / "vesta.db")
    await store.initialize()
    gate = DesktopApprovalGate(store)

    runtime = AgentRuntime(
        registry,
        tools,
        provider="fake",
        approval_gate=gate,
    )
    result_task = asyncio.create_task(
        runtime.run("执行需要审批的任务", run_id="run-1")
    )
    for _ in range(500):
        pending = await store.list(status=ApprovalRequestStatus.PENDING)
        if pending:
            break
        await asyncio.sleep(0)

    await gate.deny(pending[0].id)
    result = await asyncio.wait_for(result_task, timeout=10)

    assert tool.executions == 0
    assert result.tool_calls[0].result.success is False
    assert "denied" in (result.tool_calls[0].result.error or "")
