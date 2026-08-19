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

from app.agent.events import AgentEventHandler, CompositeEventHandler
from app.agent.result import AgentResult
from app.trace import SQLiteTraceEventHandler, SQLiteTraceStore

from .inputs import ConversationSource, TriggerContext
from .store import SQLiteConversationStore

if TYPE_CHECKING:  # 避免 app.context ↔ app.conversation 循环导入
    from app.context import SQLiteConversationSummaryStore
    from app.run import Run, RunManager

logger = logging.getLogger("oneagent.conversation.service")


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
    ) -> DispatchResult:
        """投递一条输入并完整执行（加载最新 → Run → 写回 → Summary）。

        ``event_handler`` 是可选的额外观察者（如 CLI 打印）；Trace 总是由本
        Service 统一注入，Automation 与手动输入走同一条 Trace 路径。

        ``on_run_started`` 可选：Run 创建并拿到 run_id 后、等待完成前被调用
        （``await on_run_started(run_id)``）。Automation 用它立即持久化
        last_run_id —— Run 已启动即记录，崩溃后不会重复补跑。
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
            )

    async def _dispatch_locked(
        self,
        *,
        conversation_id: str | None,
        content: str,
        trigger: TriggerContext,
        event_handler: AgentEventHandler | None,
        on_run_started: Any | None,
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

        # 3) 启动 Run（携带 provenance）并等待完成。
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

    async def is_run_running(self, run_id: str) -> bool:
        """供 Scheduler 做 max_instances 检查（只读 Run 生命周期查询）。"""

        run = await self._run_manager.get_run(run_id)
        return run is not None and run.status.value == "running"


__all__ = ["ConversationService", "DispatchResult"]
