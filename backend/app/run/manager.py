"""Run Manager V1：统一创建、查询、取消和恢复 Agent Run 的生命周期。

职责边界（本模块不做的事）：
- 不复制 AgentRuntime 的 agent loop（直接复用现有 ``AgentRuntime.run_stream``）；
- 不复制 Checkpoint / Trace 数据结构；
- 不构建 Context、不执行 Tool、不加载 Skill、不推进 Task。

RunManager 只负责“Run 生命周期对象”：把一次 Agent 执行包装成可持久化、
可查询、可取消、可恢复的 Run，并负责进程重启后的 reconciliation。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.agent.events import AgentEventHandler
from app.agent.result import AgentResult
from app.agent.runtime import AgentRuntime
from app.checkpoint import CheckpointStatus, SQLiteCheckpointStore
from app.context import ConversationSummaryState

from .models import Run, RunStatus
from .store import SQLiteRunStore

_STALE_RUN_ERROR = "process restarted; run did not reach a terminal state"


class RunManager:
    """Run 生命周期管理入口（V1：start / get / list / cancel / recover）。"""

    def __init__(
        self,
        run_store: SQLiteRunStore,
        checkpoint_store: SQLiteCheckpointStore,
        runtime: AgentRuntime,
    ) -> None:
        self._run_store = run_store
        self._checkpoint_store = checkpoint_store
        self._runtime = runtime
        # 进程内正在执行的 Run → asyncio.Task（用于 cancel / wait）。
        self._active_tasks: dict[str, asyncio.Task[None]] = {}
        # 会话内最近一次执行完成的 AgentResult（供 CLI 读取，不持久化）。
        self._last_results: dict[str, AgentResult] = {}

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def initialize(self) -> tuple[Run, ...]:
        """建表并执行启动 reconciliation，返回被修正的陈旧 Run。"""

        await self._run_store.initialize()
        return await self.reconcile()

    async def reconcile(self) -> tuple[Run, ...]:
        """进程重启后的统一 reconciliation（Run 与 Checkpoint 一起处理）。

        RUNNING 是进程级事实：进程重启后，当前进程内不可能存在这些 Run 的
        Agent execution，不能让它永远留在 RUNNING。所有启动恢复逻辑统一收敛
        到这里（CLI 只展示结果、不再直接改生命周期状态）。

        顺序：
        1. Checkpoint 层：先把所有遗留 RUNNING Checkpoint 转 INTERRUPTED
           （checkpoint.recover_running 不区分会话，处理全部遗留记录）；
        2. Run 层：基于 Checkpoint 事实修正 RUNNING Run：
           - 有 Checkpoint（已转 INTERRUPTED）→ Run 转 INTERRUPTED；
           - Checkpoint 已是 COMPLETED / FAILED → Run 同步为对应终态
             （Checkpoint 是“执行边界”的事实源，说明 Run 状态更新滞后）；
           - 不存在 Checkpoint → Run 转 FAILED（没有可恢复状态）。
        """

        if self._checkpoint_store is not None:
            await self._checkpoint_store.recover_running()
        stale = await self._run_store.list_runs(status=RunStatus.RUNNING)
        reconciled: list[Run] = []
        for run in stale:
            checkpoint = await self._checkpoint_store.get(run.id)
            if checkpoint is None:
                updated = await self._run_store.mark_failed(
                    run.id,
                    error=_STALE_RUN_ERROR
                    + " (no recoverable checkpoint)",
                )
            elif checkpoint.status is CheckpointStatus.INTERRUPTED:
                updated = await self._run_store.mark_interrupted(
                    run.id,
                    error=_STALE_RUN_ERROR
                    + " (recoverable checkpoint preserved)",
                )
            elif checkpoint.status is CheckpointStatus.COMPLETED:
                updated = await self._run_store.mark_completed(
                    run.id,
                    stop_reason=(
                        checkpoint.stop_reason.value
                        if checkpoint.stop_reason is not None
                        else None
                    ),
                )
            else:  # FAILED
                updated = await self._run_store.mark_failed(
                    run.id,
                    error=checkpoint.error,
                )
            reconciled.append(updated)
        return tuple(reconciled)

    # ------------------------------------------------------------------
    # start / get / list
    # ------------------------------------------------------------------

    async def start(
        self,
        user_message: str,
        *,
        conversation_id: str | None = None,
        history: tuple[Any, ...] = (),
        summary_state: ConversationSummaryState | None = None,
        event_handler: AgentEventHandler | None = None,
        recovery_run_id: str | None = None,
        recovered_from_run_id: str | None = None,
    ) -> tuple[str, asyncio.Task[None]]:
        """创建一个新 Run 并开始异步执行，返回 (run_id, task)。

        Run 生命周期：PENDING → RUNNING →（执行结束后）COMPLETED / FAILED /
        CANCELLED / INTERRUPTED。调用方通过 ``wait()`` 等待完成，或通过
        ``cancel()`` 主动取消。
        """

        run = await self._run_store.create(
            conversation_id=conversation_id,
            user_message=user_message,
            recovered_from_run_id=recovered_from_run_id,
        )
        await self._run_store.mark_started(run.id)
        task = asyncio.create_task(
            self._execute(
                run.id,
                user_message=user_message,
                conversation_id=conversation_id,
                history=history,
                summary_state=summary_state,
                event_handler=event_handler,
                recovery_run_id=recovery_run_id,
            )
        )
        self._active_tasks[run.id] = task
        return run.id, task

    async def wait(self, run_id: str) -> Run:
        """等待 Run 执行结束并返回最终 Run 记录（幂等，可多次调用）。"""

        task = self._active_tasks.get(run_id)
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                # 取消后的 Run 状态已由 _execute 更新为 CANCELLED。
                pass
        return await self._run_store.require(run_id)

    def result(self, run_id: str) -> AgentResult | None:
        """返回本进程内最近一次执行的 AgentResult（用于 CLI 读取最终消息）。"""

        return self._last_results.get(run_id)

    async def get_run(self, run_id: str) -> Run | None:
        return await self._run_store.get(run_id)

    async def list_runs(
        self,
        *,
        conversation_id: str | None = None,
        status: RunStatus | str | None = None,
        limit: int = 20,
    ) -> tuple[Run, ...]:
        return await self._run_store.list_runs(
            conversation_id=conversation_id,
            status=status,
            limit=limit,
        )

    @property
    def active_run_ids(self) -> tuple[str, ...]:
        return tuple(
            run_id
            for run_id, task in self._active_tasks.items()
            if not task.done()
        )

    # ------------------------------------------------------------------
    # cancel
    # ------------------------------------------------------------------

    async def cancel(self, run_id: str) -> Run:
        """取消正在执行的 Run。

        通过取消底层 asyncio.Task 向 AgentRuntime 发出取消信号：
        - Agent loop 在安全边界（checkpoint 保存点 / 工具执行 await 点）停止；
        - 不会启动新的 Agent Step / Tool（循环被取消）；
        - 已记录的 Trace 事件保留在 TraceStore；
        - AgentRuntime 会把 Checkpoint 转 INTERRUPTED（保留 pending_tool_calls /
          completed_tool_results，未决工具语义为“不确定，禁止直接重试”）；
        - Run 最终状态为 CANCELLED（终态）。

        已经进入终态的 Run 不能被取消（由状态转换校验拒绝）。
        """

        run = await self._run_store.require(run_id)
        if run.status is not RunStatus.RUNNING:
            raise ValueError(
                f"cannot cancel run in state {run.status.value}"
            )
        task = self._active_tasks.get(run_id)
        if task is None or task.done():
            # 进程内没有活跃执行（例如记录恢复自持久化的 RUNNING 但 task 已消失），
            # 无法发送取消信号 —— 直接标记 CANCELLED。
            return await self._run_store.mark_cancelled(
                run_id,
                error="cancelled without active execution",
            )
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return await self._run_store.require(run_id)

    # ------------------------------------------------------------------
    # recover
    # ------------------------------------------------------------------

    async def recover(
        self,
        run_id: str,
        *,
        history: tuple[Any, ...] = (),
        summary_state: ConversationSummaryState | None = None,
        event_handler: AgentEventHandler | None = None,
    ) -> tuple[str, asyncio.Task[None]]:
        """恢复一个 INTERRUPTED Run。

        复用现有 Checkpoint 恢复协议（不重造第二套）：
        - 校验目标 Run 是 INTERRUPTED 且存在可恢复（INTERRUPTED 且未 recovered）
          Checkpoint；
        - 以同一个 conversation 启动一个新的 Run，把旧 Run 的 Checkpoint 通过
          ``recovery_run_id`` 交给 AgentRuntime；
        - AgentRuntime 会注入恢复证据（render_checkpoint_context），让模型基于
          completed_tool_results 继续、把 pending_tool_calls 视为不确定，
          不会重复已确认完成的 Tool Call；
        - 新 Run 正常结束后，Checkpoint 层 ``mark_recovered`` 把旧中断标记为已处理；
        - 旧 Run 保持 INTERRUPTED（生命周期事实），新 Run 记录
          recovered_from_run_id 指向旧 Run。
        """

        run = await self._run_store.require(run_id)
        if run.status is not RunStatus.INTERRUPTED:
            raise ValueError(
                f"only interrupted run can be recovered: {run_id} "
                f"({run.status.value})"
            )
        checkpoint = await self._checkpoint_store.get_unrecovered(run_id)
        if checkpoint is None:
            raise ValueError(
                f"no recoverable checkpoint for run {run_id}"
            )
        return await self.start(
            checkpoint.user_message.content or run.user_message,
            conversation_id=run.conversation_id,
            history=history,
            summary_state=summary_state,
            event_handler=event_handler,
            recovery_run_id=run_id,
            recovered_from_run_id=run_id,
        )

    # ------------------------------------------------------------------
    # 内部执行
    # ------------------------------------------------------------------

    async def _execute(
        self,
        run_id: str,
        *,
        user_message: str,
        conversation_id: str | None,
        history: tuple[Any, ...],
        summary_state: ConversationSummaryState | None,
        event_handler: AgentEventHandler | None,
        recovery_run_id: str | None,
    ) -> None:
        try:
            result: AgentResult | None = None
            try:
                async for event in self._runtime.run_stream(
                    user_message,
                    history=history,
                    conversation_id=conversation_id,
                    event_handler=event_handler,
                    summary_state=summary_state,
                    run_id=run_id,
                    recovery_run_id=recovery_run_id,
                ):
                    if event.result is not None:
                        result = event.result
            except asyncio.CancelledError:
                await self._run_store.mark_cancelled(
                    run_id,
                    error="cancelled by user",
                )
                raise
            except BaseException as exc:  # noqa: BLE001
                await self._run_store.mark_failed(
                    run_id,
                    error=f"{type(exc).__name__}: {exc}",
                )
                return

            if result is None:
                await self._run_store.mark_failed(
                    run_id,
                    error="agent produced no result",
                )
                return
            self._last_results[run_id] = result
            if result.ok:
                await self._run_store.mark_completed(
                    run_id,
                    stop_reason=result.stop_reason.value,
                )
            else:
                await self._run_store.mark_failed(
                    run_id,
                    error=(
                        result.error.message
                        if result.error is not None
                        else result.stop_reason.value
                    ),
                    stop_reason=result.stop_reason.value,
                )
        finally:
            self._active_tasks.pop(run_id, None)


__all__ = ["RunManager"]
