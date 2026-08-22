"""Task Desktop RPC 的会话隔离测试。"""

from types import SimpleNamespace

import pytest

from app.server.rpc.dispatcher import RpcContext
from app.server.rpc.methods import tasks as tasks_rpc
from app.server.rpc.protocol import JsonRpcError, RpcErrorCode
from app.task import FileTaskStore

pytestmark = pytest.mark.asyncio


async def test_task_list_only_returns_current_conversation(tmp_path) -> None:
    store = FileTaskStore(tmp_path / "tasks")
    await store.initialize()
    task_a = await store.create(title="会话 A 任务", owner_conversation_id="conv-a")
    await store.create(title="会话 B 任务", owner_conversation_id="conv-b")
    ctx = RpcContext(SimpleNamespace(task_store=store), SimpleNamespace())

    result = await tasks_rpc.task_list({"conversation_id": "conv-a"}, ctx)

    assert [task.id for task in result["tasks"]] == [task_a.id]
    assert all(task.owner_conversation_id == "conv-a" for task in result["tasks"])


async def test_task_list_requires_conversation_id(tmp_path) -> None:
    store = FileTaskStore(tmp_path / "tasks")
    await store.initialize()
    ctx = RpcContext(SimpleNamespace(task_store=store), SimpleNamespace())

    with pytest.raises(JsonRpcError) as exc_info:
        await tasks_rpc.task_list({}, ctx)

    assert exc_info.value.code == RpcErrorCode.INVALID_PARAMS
