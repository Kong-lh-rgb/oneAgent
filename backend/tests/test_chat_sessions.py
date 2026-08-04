from __future__ import annotations

from collections.abc import Sequence

import pytest

from app.agent.events import AgentEvent, AgentEventHandler, AgentEventType
from app.agent.result import AgentResult, AgentStopReason
from app.conversation import SQLiteConversationStore
from app.models.chat import _load_or_create_conversation, _send_message
from app.models.types import Message, MessageRole, ModelProvider
from app.trace import SQLiteTraceEventHandler, SQLiteTraceStore


class StubRuntime:
    async def run(
        self,
        user_input: str,
        *,
        history: Sequence[Message] = (),
        conversation_id: str | None = None,
    ) -> AgentResult:
        user_message = Message(role=MessageRole.USER, content=user_input)
        final_message = Message(role=MessageRole.ASSISTANT, content="已完成")
        return AgentResult(
            run_id="stub-run",
            final_message=final_message,
            messages=(*history, user_message, final_message),
            steps=1,
            stop_reason=AgentStopReason.FINAL_ANSWER,
        )

    async def run_stream(
        self,
        user_input: str,
        *,
        history: Sequence[Message] = (),
        conversation_id: str | None = None,
        event_handler: AgentEventHandler | None = None,
    ):
        result = await self.run(
            user_input,
            history=history,
            conversation_id=conversation_id,
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
