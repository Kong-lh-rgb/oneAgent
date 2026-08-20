"""Computer Machine Lease 与 Tool Hook 的纯离线测试。"""

from __future__ import annotations

import os
from typing import Any

import pytest

from app.computer import (
    ComputerBusyError,
    ComputerLeaseHook,
    ComputerLeaseManager,
    ComputerObserveTool,
    FakeComputerRuntime,
    register_computer_tools,
)
from app.models.types import ToolCall, ToolDefinition
from app.tools import (
    AutoApproveGate,
    BaseTool,
    ToolExecutionContext,
    ToolExecutor,
    ToolRegistry,
)


def test_machine_lease_owner_lifecycle(tmp_path) -> None:
    manager = ComputerLeaseManager(tmp_path / "machine.lock")
    first = manager.acquire("run-a")
    again = manager.acquire("run-a")
    assert first.owner_run_id == again.owner_run_id == "run-a"
    assert first.acquired_at == again.acquired_at
    with pytest.raises(ComputerBusyError, match="another run"):
        manager.acquire("run-b")
    assert manager.release("run-b") is False
    assert manager.snapshot.owner_run_id == "run-a"
    assert manager.release("run-a") is True
    assert manager.acquire("run-b").owner_run_id == "run-b"
    manager.close()
    manager.close()
    assert manager.snapshot.owner_run_id is None


def test_flock_blocks_second_manager(tmp_path) -> None:
    path = tmp_path / "machine.lock"
    first = ComputerLeaseManager(path)
    second = ComputerLeaseManager(path)
    first.acquire("run-a")
    with pytest.raises(ComputerBusyError):
        second.acquire("run-b")
    first.close()
    assert second.acquire("run-b").owner_run_id == "run-b"
    second.close()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="fcntl/fork is macOS-only")
def test_flock_blocks_another_host_process(tmp_path) -> None:
    """子进程模拟第二个 Vesta Host，真正验证跨进程 flock。"""

    lock_path = tmp_path / "machine.lock"
    ready_read, ready_write = os.pipe()
    release_read, release_write = os.pipe()
    pid = os.fork()
    if pid == 0:
        try:
            os.close(ready_read)
            os.close(release_write)
            lease = ComputerLeaseManager(lock_path)
            lease.acquire("child-run")
            os.write(ready_write, b"1")
            os.read(release_read, 1)
            lease.close()
            os._exit(0)
        except BaseException:
            os._exit(1)

    os.close(ready_write)
    os.close(release_read)
    assert os.read(ready_read, 1) == b"1"
    contender = ComputerLeaseManager(lock_path)
    try:
        with pytest.raises(ComputerBusyError):
            contender.acquire("parent-run")
    finally:
        os.write(release_write, b"1")
        os.close(ready_read)
        os.close(release_write)
        _, status = os.waitpid(pid, 0)
        contender.close()
    assert os.waitstatus_to_exitcode(status) == 0


class PlainTool(BaseTool):
    definition = ToolDefinition(name="plain", description="普通工具")

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, arguments: dict[str, Any]) -> str:
        self.calls += 1
        return "ok"


async def test_computer_hook_requires_run_context_but_plain_tool_does_not(
    tmp_path,
) -> None:
    lease = ComputerLeaseManager(tmp_path / "machine.lock")
    fake = FakeComputerRuntime()
    plain = PlainTool()
    registry = ToolRegistry()
    registry.register(ComputerObserveTool(fake))
    registry.register(plain)
    executor = ToolExecutor(registry, hooks=(ComputerLeaseHook(lease),))

    computer = await executor.execute(
        ToolCall(id="c", name="computer_observe", arguments={})
    )
    normal = await executor.execute(ToolCall(id="p", name="plain", arguments={}))
    assert computer.success is False
    assert "requires run context" in (computer.error or "")
    assert normal.success is True and plain.calls == 1


async def test_busy_run_does_not_call_computer_runtime(tmp_path) -> None:
    lease = ComputerLeaseManager(tmp_path / "machine.lock")
    fake = FakeComputerRuntime()
    registry = ToolRegistry()
    registry.register(ComputerObserveTool(fake))
    executor = ToolExecutor(registry, hooks=(ComputerLeaseHook(lease),))
    call_a = ToolCall(id="a", name="computer_observe", arguments={})
    call_b = ToolCall(id="b", name="computer_observe", arguments={})

    first = await executor.execute(
        call_a, context=ToolExecutionContext(tool_call=call_a, run_id="run-a")
    )
    second = await executor.execute(
        call_b, context=ToolExecutionContext(tool_call=call_b, run_id="run-b")
    )
    assert first.success is True
    assert second.success is False
    assert "another run" in (second.error or "")
    assert lease.snapshot.owner_run_id == "run-a"
    lease.close()


async def test_same_run_reuses_lease_for_observe_and_action(tmp_path) -> None:
    lease = ComputerLeaseManager(tmp_path / "machine.lock")
    fake = FakeComputerRuntime()
    registry = ToolRegistry()
    register_computer_tools(registry, fake)
    executor = ToolExecutor(registry, hooks=(ComputerLeaseHook(lease),))
    observe = ToolCall(id="o", name="computer_observe", arguments={})
    scroll = ToolCall(id="s", name="computer_scroll", arguments={"delta_y": -1})
    assert (
        await executor.execute(
            observe,
            context=ToolExecutionContext(tool_call=observe, run_id="run-a"),
        )
    ).success
    assert (
        await executor.execute(
            scroll,
            context=ToolExecutionContext(tool_call=scroll, run_id="run-a"),
        )
    ).success
    assert lease.snapshot.owner_run_id == "run-a"
    assert [item.action.value for item in fake.action_history] == ["scroll"]
    lease.close()


async def test_busy_denial_overrides_human_approval_decision(tmp_path) -> None:
    """Lease denial must not be hidden by PermissionHook's approval request."""

    lease = ComputerLeaseManager(tmp_path / "machine.lock")
    lease.acquire("run-a")
    fake = FakeComputerRuntime()
    registry = ToolRegistry()
    register_computer_tools(registry, fake)
    executor = ToolExecutor(
        registry,
        approval_gate=AutoApproveGate(),
        hooks=(ComputerLeaseHook(lease),),
    )
    click = ToolCall(
        id="c",
        name="computer_click",
        arguments={"observation_id": "obs-1", "element_ref": "e1"},
    )
    result = await executor.execute(
        click,
        context=ToolExecutionContext(tool_call=click, run_id="run-b"),
    )
    assert result.success is False
    assert "another run" in (result.error or "")
    assert fake.action_history == []
    lease.close()
