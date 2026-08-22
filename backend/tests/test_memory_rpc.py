"""长期记忆 Desktop 只读 RPC 测试。"""

from types import SimpleNamespace

import pytest

from app.memory import MemoryManager
from app.server.rpc.dispatcher import RpcContext
from app.server.rpc.methods import memories as memories_rpc

pytestmark = pytest.mark.asyncio


async def test_memory_list_returns_core_active_and_archived(tmp_path) -> None:
    manager = MemoryManager(tmp_path / "memory", max_active=5)
    await manager.initialize()
    await manager.core.update("用户偏好中文回答。")
    active = await manager.create(
        title="项目决定",
        summary="记录项目的长期技术决定",
        content="使用 Markdown Memory。",
    )
    archived = await manager.create(
        title="旧决定",
        summary="已经过期的旧决定",
        content="旧的实现方案。",
    )
    await manager.archive(archived.id, reason="方案已替换")
    ctx = RpcContext(SimpleNamespace(memory_manager=manager), SimpleNamespace())

    result = await memories_rpc.memory_list({}, ctx)

    assert result["core"] == "用户偏好中文回答。"
    assert result["active_count"] == 1
    assert result["max_active"] == 5
    assert result["active"][0]["id"] == active.id
    assert result["active"][0]["content"] == "使用 Markdown Memory。"
    assert result["archived"][0]["id"] == archived.id
    assert result["archived"][0]["archive_reason"] == "方案已替换"


async def test_memory_list_without_manager_returns_empty_view() -> None:
    ctx = RpcContext(SimpleNamespace(memory_manager=None), SimpleNamespace())
    result = await memories_rpc.memory_list({}, ctx)
    assert result == {
        "core": "",
        "active": [],
        "archived": [],
        "active_count": 0,
        "max_active": 0,
    }
