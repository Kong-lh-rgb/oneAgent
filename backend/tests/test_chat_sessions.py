from __future__ import annotations

import sys
from collections.abc import Sequence

import pytest

from app.agent.events import AgentEvent, AgentEventHandler, AgentEventType
from app.agent.result import AgentResult, AgentStopReason
from app.context import (
    ConversationSummaryState,
    RollingConversationSummary,
    SQLiteConversationSummaryStore,
)
from app.conversation import SQLiteConversationStore
from app.models.chat import (
    _load_or_create_conversation,
    _parse_args,
    _print_permission_rules,
    _remove_permission_rule,
    _send_message,
)
from app.models.types import Message, MessageRole, ModelProvider, ToolCall
from app.tools import ApprovalScope, SQLitePermissionRuleStore, build_safe_rule
from app.trace import SQLiteTraceEventHandler, SQLiteTraceStore


class StubRuntime:
    def __init__(self) -> None:
        self.seen_summary_state: ConversationSummaryState | None = None

    async def run(
        self,
        user_input: str,
        *,
        history: Sequence[Message] = (),
        conversation_id: str | None = None,
        summary_state: ConversationSummaryState | None = None,
    ) -> AgentResult:
        self.seen_summary_state = summary_state
        user_message = Message(role=MessageRole.USER, content=user_input)
        final_message = Message(role=MessageRole.ASSISTANT, content="已完成")
        return AgentResult(
            run_id="stub-run",
            final_message=final_message,
            messages=(*history, user_message, final_message),
            steps=1,
            stop_reason=AgentStopReason.FINAL_ANSWER,
            summary_state=summary_state,
        )

    async def run_stream(
        self,
        user_input: str,
        *,
        history: Sequence[Message] = (),
        conversation_id: str | None = None,
        event_handler: AgentEventHandler | None = None,
        summary_state: ConversationSummaryState | None = None,
    ):
        result = await self.run(
            user_input,
            history=history,
            conversation_id=conversation_id,
            summary_state=summary_state,
        )
        events = (
            AgentEvent(
                run_id=result.run_id,
                conversation_id=conversation_id,
                type=AgentEventType.AGENT_STARTED,
            ),
            AgentEvent(
                run_id=result.run_id,
                conversation_id=conversation_id,
                sequence=1,
                type=AgentEventType.AGENT_COMPLETED,
                stop_reason=result.stop_reason,
                usage=result.usage,
                result=result,
            ),
        )
        for event in events:
            if event_handler is not None:
                await event_handler.emit(event)
            yield event


@pytest.mark.asyncio
async def test_cli_restores_latest_conversation_after_restart(tmp_path) -> None:
    database_path = tmp_path / "oneagent.db"
    store = SQLiteConversationStore(database_path)
    await store.initialize()
    created, history, resumed = await _load_or_create_conversation(
        store,
        identifier=None,
        force_new=False,
        system_prompt="系统提示",
    )
    await store.replace_messages(
        created.id,
        (*history, Message(role=MessageRole.USER, content="第一轮消息")),
    )

    reopened_store = SQLiteConversationStore(database_path)
    await reopened_store.initialize()
    restored, restored_history, resumed = await _load_or_create_conversation(
        reopened_store,
        identifier=None,
        force_new=False,
        system_prompt="不会覆盖已有会话",
    )

    assert resumed is True
    assert restored.id == created.id
    assert [message.content for message in restored_history] == [
        "系统提示",
        "第一轮消息",
    ]


@pytest.mark.asyncio
async def test_cli_can_force_new_or_restore_by_short_id(tmp_path) -> None:
    store = SQLiteConversationStore(tmp_path / "oneagent.db")
    await store.initialize()
    first = await store.create(title="已有会话")

    selected, _, resumed = await _load_or_create_conversation(
        store,
        identifier=first.id[:8],
        force_new=False,
        system_prompt=None,
    )
    created, _, created_resumed = await _load_or_create_conversation(
        store,
        identifier=None,
        force_new=True,
        system_prompt="新系统提示",
    )

    assert resumed is True
    assert selected.id == first.id
    assert created_resumed is False
    assert created.id != first.id
    assert (await store.load_messages(created.id))[0].content == "新系统提示"


@pytest.mark.asyncio
async def test_send_message_persists_runtime_history_and_generates_title(
    tmp_path,
    capsys,
) -> None:
    store = SQLiteConversationStore(tmp_path / "oneagent.db")
    await store.initialize()
    trace_store = SQLiteTraceStore(tmp_path / "oneagent.db")
    await trace_store.initialize()
    conversation = await store.create(
        messages=(Message(role=MessageRole.SYSTEM, content="系统提示"),)
    )
    history = list(await store.load_messages(conversation.id))

    success, updated = await _send_message(
        runtime=StubRuntime(),  # type: ignore[arg-type]
        conversation_store=store,
        trace_handler=SQLiteTraceEventHandler(trace_store),
        conversation=conversation,
        provider=ModelProvider.OPENAI,
        history=history,
        content="请读取本地项目并给出一份详细总结",
        model="fake-model",
    )

    assert success is True
    assert updated.title == "请读取本地项目并给出一份详细总结"
    assert await store.load_messages(conversation.id) == tuple(history)
    assert [message.role for message in history] == [
        MessageRole.SYSTEM,
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]
    output = capsys.readouterr().out
    assert "Agent 开始执行" in output
    assert "Agent 执行完成" in output
    assert "OneAgent> 已完成" in output
    runs = await trace_store.list_runs()
    assert len(runs) == 1
    assert runs[0].run_id == "stub-run"
    assert runs[0].conversation_id == conversation.id
    assert runs[0].event_count == 2


@pytest.mark.asyncio
async def test_send_message_restores_and_persists_summary_state(tmp_path) -> None:
    database_path = tmp_path / "oneagent.db"
    conversation_store = SQLiteConversationStore(database_path)
    await conversation_store.initialize()
    summary_store = SQLiteConversationSummaryStore(database_path)
    await summary_store.initialize()
    trace_store = SQLiteTraceStore(database_path)
    await trace_store.initialize()
    conversation = await conversation_store.create()
    state = ConversationSummaryState(
        summary=RollingConversationSummary(current_objective="继续测试"),
        covered_message_count=0,
    )
    await summary_store.save(conversation.id, state)
    runtime = StubRuntime()
    history: list[Message] = []

    await _send_message(
        runtime=runtime,  # type: ignore[arg-type]
        conversation_store=conversation_store,
        trace_handler=SQLiteTraceEventHandler(trace_store),
        conversation=conversation,
        provider=ModelProvider.OPENAI,
        history=history,
        content="继续",
        model="fake-model",
        summary_store=summary_store,
    )

    assert runtime.seen_summary_state == state
    assert await summary_store.load(conversation.id) == state


@pytest.mark.asyncio
async def test_cli_persists_and_restores_complete_tool_protocol_history(
    tmp_path,
    capsys,
) -> None:
    store = SQLiteConversationStore(tmp_path / "oneagent.db")
    await store.initialize()
    trace_store = SQLiteTraceStore(tmp_path / "oneagent.db")
    await trace_store.initialize()
    conversation = await store.create()
    history: list[Message] = []
    call = ToolCall(id="search-1", name="web_search", arguments={"query": "AI"})

    class ToolProtocolRuntime(StubRuntime):
        async def run(
            self,
            user_input: str,
            *,
            history: Sequence[Message] = (),
            conversation_id: str | None = None,
            summary_state: ConversationSummaryState | None = None,
        ) -> AgentResult:
            user_message = Message(role=MessageRole.USER, content=user_input)
            tool_call_message = Message(
                role=MessageRole.ASSISTANT,
                tool_calls=(call,),
            )
            tool_message = Message(
                role=MessageRole.TOOL,
                tool_call_id=call.id,
                name=call.name,
                content="搜索原始结果",
            )
            final_message = Message(role=MessageRole.ASSISTANT, content="搜索摘要")
            return AgentResult(
                run_id="tool-run",
                final_message=final_message,
                messages=(
                    *history,
                    user_message,
                    tool_call_message,
                    tool_message,
                    final_message,
                ),
                steps=2,
                stop_reason=AgentStopReason.FINAL_ANSWER,
                summary_state=summary_state,
            )

    await _send_message(
        runtime=ToolProtocolRuntime(),  # type: ignore[arg-type]
        conversation_store=store,
        trace_handler=SQLiteTraceEventHandler(trace_store),
        conversation=conversation,
        provider=ModelProvider.OPENAI,
        history=history,
        content="搜索 AI",
        model="fake-model",
    )
    _, restored_history, resumed = await _load_or_create_conversation(
        store,
        identifier=conversation.id,
        force_new=False,
        system_prompt=None,
    )

    assert resumed is True
    assert restored_history == history
    assert [message.role for message in restored_history] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
    ]
    assert restored_history[1].tool_calls == (call,)
    assert restored_history[2].tool_call_id == call.id
    capsys.readouterr()


@pytest.mark.asyncio
async def test_cli_lists_and_removes_conversation_permission_rules(
    tmp_path,
    capsys,
) -> None:
    store = SQLitePermissionRuleStore(tmp_path / "oneagent.db")
    await store.initialize()
    rule = build_safe_rule(
        tool_name="run_shell_command",
        arguments={"command": "pytest x"},
        scope=ApprovalScope.CONVERSATION,
        scope_id="conversation-1",
    )
    await store.add(rule)

    _print_permission_rules(await store.list(scope_ids=("conversation-1",)))
    output = capsys.readouterr().out
    assert rule.id[:8] in output
    assert rule.description in output

    assert (
        await _remove_permission_rule(
            store,
            "conversation-1",
            rule.id[:8],
        )
        is True
    )
    assert await store.list(scope_ids=("conversation-1",)) == ()


def test_cli_uses_provider_default_output_tokens_when_unspecified(
    monkeypatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["oneagent-chat"])

    args = _parse_args()

    assert args.max_output_tokens is None


def test_cli_accepts_explicit_output_tokens(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["oneagent-chat", "--max-output-tokens", "8192"],
    )

    args = _parse_args()

    assert args.max_output_tokens == 8192
