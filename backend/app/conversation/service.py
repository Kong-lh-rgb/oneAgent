"""ConversationService：统一的 Conversation 输入执行链。

职责：把“一条输入投递到某个 Conversation”完整执行到底，并统一接入 Trace：

    load 最新持久化 history
      → load Conversation Summary
      → RunManager.start()
      → 等待 Run 完成（含 Trace handler 注入）
      → 获取 AgentResult
      → 把完整 Conversation history 写回 ConversationStore
      → 保存最新 Summary
      → 返回 DispatchResult(run, result, trigger)

CLI、Automation、未来 API / Desktop 都通过它执行，不再各自维护一套
load→start→wait→save 逻辑。本 Service 不包含任何 CLI print/input 逻辑。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.agent.events import (
    AgentEvent,
    AgentEventHandler,
    AgentEventType,
    CompositeEventHandler,
)
from app.agent.result import AgentResult, AgentStopReason
from app.models.types import AgentMode, Message, MessageRole
from app.run.models import RunStatus
from app.trace import SQLiteTraceEventHandler, SQLiteTraceStore

from .inputs import ConversationSource, TriggerContext
from .store import SQLiteConversationStore

if TYPE_CHECKING:  # 避免 app.context ↔ app.conversation 循环导入
    from app.context import SQLiteConversationSummaryStore
    from app.run import Run, RunManager

logger = logging.getLogger("vesta.conversation.service")


@dataclass
class DispatchResult:
    """一次 Conversation 输入投递的完整结果。"""

    run: Run
    result: AgentResult
    trigger: TriggerContext
    conversation_id: str | None


class _NullLock:
    """无会话（conversation_id=None）时使用的空锁：不串行化。"""

    async def __aenter__(self) -> _NullLock:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


_NULL_LOCK = _NullLock()


class ConversationService:
    """统一执行一次 Conversation 输入（手动 / Automation 同路径）。

    同一 conversation 的 dispatch 按会话串行（per-conversation asyncio.Lock），
    避免两个 Run 同时 load history 后互相 replace_messages 导致消息丢失；
    不同 conversation 仍可并行。
    """

    def __init__(
        self,
        conversation_store: SQLiteConversationStore,
        run_manager: RunManager,
        trace_store: SQLiteTraceStore,
        *,
        summary_store: SQLiteConversationSummaryStore | None = None,
        shared_event_handler: AgentEventHandler | None = None,
    ) -> None:
        self._conversation_store = conversation_store
        self._run_manager = run_manager
        self._trace_store = trace_store
        self._summary_store = summary_store
        # 全局共享观察者（如 Desktop WebSocket 广播）：始终与 Trace 一起组合，
        # 手动 / Automation 触发的 Run 最终都进入同一条 broadcast path。
        self._shared_event_handler = shared_event_handler
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, conversation_id: str | None) -> Any:
        if conversation_id is None:
            return _NULL_LOCK
        lock = self._locks.get(conversation_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[conversation_id] = lock
        return lock

    async def dispatch(
        self,
        *,
        conversation_id: str | None,
        content: str,
        trigger: TriggerContext | None = None,
        event_handler: AgentEventHandler | None = None,
        on_run_started: Any | None = None,
        mode: AgentMode = AgentMode.NORMAL,
    ) -> DispatchResult:
        """投递一条输入并完整执行（加载最新 → Run → 写回 → Summary）。

        ``event_handler`` 是可选的额外观察者（如 CLI 打印）；Trace 总是由本
        Service 统一注入，Automation 与手动输入走同一条 Trace 路径。

        ``on_run_started`` 可选：Run 创建并拿到 run_id 后、等待完成前被调用
        （``await on_run_started(run_id)``）。Automation 用它立即持久化
        last_run_id —— Run 已启动即记录，崩溃后不会重复补跑。

        ``mode`` 可选：本次执行的模式（NORMAL / PLAN），默认 NORMAL。
        """

        trigger = trigger or TriggerContext(
            source=ConversationSource.MANUAL
        )
        async with self._lock_for(conversation_id):
            return await self._dispatch_locked(
                conversation_id=conversation_id,
                content=content,
                trigger=trigger,
                event_handler=event_handler,
                on_run_started=on_run_started,
                mode=mode,
            )

    async def _dispatch_locked(
        self,
        *,
        conversation_id: str | None,
        content: str,
        trigger: TriggerContext,
        event_handler: AgentEventHandler | None,
        on_run_started: Any | None,
        mode: AgentMode,
    ) -> DispatchResult:
        # 1) 从持久化源加载“触发那一刻最新”的 history / summary。
        history: tuple[Any, ...] = ()
        if conversation_id is not None:
            history = tuple(
                await self._conversation_store.load_messages(conversation_id)
            )
        summary_state = (
            await self._summary_store.load(conversation_id)
            if self._summary_store is not None and conversation_id is not None
            else None
        )

        # 2) 统一注入 Trace handler（+ 全局共享观察者 + 本次额外观察者）。
        trace_handler = SQLiteTraceEventHandler(self._trace_store)
        handlers: list[AgentEventHandler] = [trace_handler]
        if self._shared_event_handler is not None:
            handlers.append(self._shared_event_handler)
        if event_handler is not None:
            handlers.append(event_handler)
        handler: AgentEventHandler = (
            CompositeEventHandler(*handlers)
            if len(handlers) > 1
            else handlers[0]
        )

        # 3) 启动 Run（携带 provenance + mode）并等待完成。
        run_id, _ = await self._run_manager.start(
            content,
            conversation_id=conversation_id,
            history=history,
            summary_state=summary_state,
            event_handler=handler,
            source=trigger.source.value,
            source_id=trigger.automation_id,
            scheduled_for=trigger.scheduled_for,
            triggered_at=trigger.triggered_at,
            mode=mode,
        )
        if on_run_started is not None:
            await on_run_started(run_id)
        try:
            run = await self._run_manager.wait(run_id)
        except KeyboardInterrupt:
            # 用户 Ctrl+C：取消正在执行的 Run（不残留 task），再向上传播。
            try:
                await self._run_manager.cancel(run_id)
            except (ValueError, KeyError):
                pass
            raise
        result = self._run_manager.result(run_id)
        if result is None:
            # 用户取消：runtime 的 CancelledError 直接向上传播，不会产生
            # AgentResult，conversation 因此无法落库 —— 本次 user 消息与中断
            # 内容一并丢失，前端还会一直卡在运行态。这里为取消的 Run 合成
            # 终态结果：落库 user 消息 + 中断说明，并 emit agent_cancelled
            # 让前端进入 cancelled 终态（本轮部分内容不再显示）。
            if run.status is RunStatus.CANCELLED:
                cancelled_message = Message(
                    role=MessageRole.ASSISTANT,
                    content=(
                        "Run cancelled：已停止，未生成最终回复。"
                        "（本轮未完成的内容不会显示）"
                    ),
                )
                result = AgentResult(
                    run_id=run_id,
                    final_message=cancelled_message,
                    messages=(
                        *history,
                        Message(role=MessageRole.USER, content=content),
                        cancelled_message,
                    ),
                    steps=0,
                    stop_reason=AgentStopReason.CANCELLED,
                )
                await handler.emit(
                    AgentEvent(
                        run_id=run_id,
                        conversation_id=conversation_id,
                        sequence=0,
                        type=AgentEventType.AGENT_CANCELLED,
                        message=cancelled_message,
                        stop_reason=AgentStopReason.CANCELLED,
                        result=result,
                    )
                )
            elif run.status is RunStatus.INTERRUPTED:
                # 暂停（中断）：合成 INTERRUPTED 终态结果并 emit agent_failed
                # (stop_reason=interrupted)，前端进入 interrupted 终态并展示
                # “Recover/继续”入口，用户可从 Checkpoint 断点恢复执行。
                interrupted_message = Message(
                    role=MessageRole.ASSISTANT,
                    content=(
                        "Run interrupted：已暂停，可从断点继续。"
                        "（点击 Recover 从保存的中断点恢复）"
                    ),
                )
                result = AgentResult(
                    run_id=run_id,
                    final_message=interrupted_message,
                    messages=(
                        *history,
                        Message(role=MessageRole.USER, content=content),
                        interrupted_message,
                    ),
                    steps=0,
                    stop_reason=AgentStopReason.INTERRUPTED,
                )
                await handler.emit(
                    AgentEvent(
                        run_id=run_id,
                        conversation_id=conversation_id,
                        sequence=0,
                        type=AgentEventType.AGENT_FAILED,
                        message=interrupted_message,
                        stop_reason=AgentStopReason.INTERRUPTED,
                        result=result,
                    )
                )
            else:
                raise RuntimeError("RunManager 未返回最终 AgentResult")

        # 4) 把新的完整 history 写回 ConversationStore（保存最新 Summary）。
        if conversation_id is not None:
            await self._conversation_store.replace_messages(
                conversation_id,
                result.messages,
            )
        if (
            self._summary_store is not None
            and conversation_id is not None
            and result.summary_state is not None
        ):
            await self._summary_store.save(
                conversation_id,
                result.summary_state,
            )

        return DispatchResult(
            run=run,
            result=result,
            trigger=trigger,
            conversation_id=conversation_id,
        )

    async def recover(
        self,
        run_id: str,
        *,
        trigger: TriggerContext | None = None,
        event_handler: AgentEventHandler | None = None,
        on_run_started: Any | None = None,
    ) -> DispatchResult:
        """恢复一个 INTERRUPTED Run，并像 dispatch 一样完整收口。

        CLI 与 RPC 的 recover 都收敛到这里，不再各自维护一条
        load→recover→wait→写回 的链路：

            load 最新持久化 history / summary
            → RunManager.recover()（生命周期只归 RunManager）
            → 等待恢复后的 Run 完成（含 Trace handler 注入）
            → 获取 AgentResult
            → 把最终 messages 写回 ConversationStore
            → 保存最新 Summary
            → 返回 DispatchResult

        recover 语义保持：旧 Run 保持 INTERRUPTED；新 Run 的
        recovered_from_run_id 指向旧 Run（由 RunManager 负责）。
        """

        run = await self._run_manager.get_run(run_id)
        if run is None:
            raise KeyError(f"Run 不存在：{run_id}")
        conversation_id = run.conversation_id
        trigger = trigger or TriggerContext(source=ConversationSource.MANUAL)
        async with self._lock_for(conversation_id):
            return await self._recover_locked(
                run_id=run_id,
                conversation_id=conversation_id,
                trigger=trigger,
                event_handler=event_handler,
                on_run_started=on_run_started,
            )

    async def _recover_locked(
        self,
        *,
        run_id: str,
        conversation_id: str | None,
        trigger: TriggerContext,
        event_handler: AgentEventHandler | None,
        on_run_started: Any | None,
    ) -> DispatchResult:
        # 1) 从持久化源加载最新 history / summary（与 dispatch 一致）。
        history: tuple[Any, ...] = ()
        if conversation_id is not None:
            history = tuple(
                await self._conversation_store.load_messages(conversation_id)
            )
        summary_state = (
            await self._summary_store.load(conversation_id)
            if self._summary_store is not None and conversation_id is not None
            else None
        )

        # 2) 统一注入 Trace handler（+ 全局共享观察者 + 本次额外观察者）。
        trace_handler = SQLiteTraceEventHandler(self._trace_store)
        handlers: list[AgentEventHandler] = [trace_handler]
        if self._shared_event_handler is not None:
            handlers.append(self._shared_event_handler)
        if event_handler is not None:
            handlers.append(event_handler)
        handler: AgentEventHandler = (
            CompositeEventHandler(*handlers)
            if len(handlers) > 1
            else handlers[0]
        )

        # 3) 由 RunManager 启动恢复后的新 Run 并等待完成。
        new_run_id, _ = await self._run_manager.recover(
            run_id,
            history=history,
            summary_state=summary_state,
            event_handler=handler,
        )
        if on_run_started is not None:
            await on_run_started(new_run_id)
        try:
            recovered_run = await self._run_manager.wait(new_run_id)
        except KeyboardInterrupt:
            # 用户 Ctrl+C：取消正在执行的恢复 Run（不残留 task），再向上传播。
            try:
                await self._run_manager.cancel(new_run_id)
            except (ValueError, KeyError):
                pass
            raise
        result = self._run_manager.result(new_run_id)
        if result is None:
            raise RuntimeError("RunManager 未返回最终 AgentResult")

        # 4) 把恢复后的完整 history 写回 ConversationStore（保存最新 Summary）。
        if conversation_id is not None:
            await self._conversation_store.replace_messages(
                conversation_id,
                result.messages,
            )
        if (
            self._summary_store is not None
            and conversation_id is not None
            and result.summary_state is not None
        ):
            await self._summary_store.save(
                conversation_id,
                result.summary_state,
            )

        return DispatchResult(
            run=recovered_run,
            result=result,
            trigger=trigger,
            conversation_id=conversation_id,
        )

    async def is_run_running(self, run_id: str) -> bool:
        """供 Scheduler 做 max_instances 检查（只读 Run 生命周期查询）。"""

        run = await self._run_manager.get_run(run_id)
        return run is not None and run.status.value == "running"


__all__ = ["ConversationService", "DispatchResult"]
