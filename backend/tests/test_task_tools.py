"""任务管理工具测试（文件系统存储）。"""

from __future__ import annotations

import json

import pytest

from app.models.types import ToolCall
from app.task import (
    FileTaskStore,
    TaskCreateTool,
    TaskGetTool,
    TaskListTool,
    TaskStatus,
    TaskStep,
    TaskStepStatus,
    TaskUpdateTool,
    register_task_tools,
)
from app.tools import ToolExecutionContext, ToolExecutor, ToolRegistry

_OWNER = "conv-test"


@pytest.fixture
async def store(tmp_path) -> FileTaskStore:
    instance = FileTaskStore(tmp_path / "tasks")
    await instance.initialize()
    return instance


def _registry(store: FileTaskStore) -> ToolRegistry:
    registry = ToolRegistry()
    register_task_tools(registry, store)
    return registry


def _context(
    tool_name: str,
    *,
    conversation_id: str = _OWNER,
    run_id: str = "run-test",
) -> ToolExecutionContext:
    call = ToolCall(id=f"{tool_name}-call", name=tool_name, arguments={})
    return ToolExecutionContext(
        tool_call=call,
        conversation_id=conversation_id,
        run_id=run_id,
    )


async def _execute(
    tool,
    arguments,
    *,
    conversation_id: str = _OWNER,
):
    return await tool.execute_with_context(
        arguments,
        _context(tool.definition.name, conversation_id=conversation_id),
    )


async def test_registers_four_task_tools(store: FileTaskStore) -> None:
    registry = _registry(store)
    assert set(registry.names()) == {
        "task_create",
        "task_update",
        "task_get",
        "task_list",
    }
    definitions = registry.definitions(for_model=True)
    assert {definition.name for definition in definitions} == set(registry.names())
    assert all(
        definition.permission.model_visible() for definition in definitions
    )


async def test_task_create_with_steps(store: FileTaskStore) -> None:
    tool = TaskCreateTool(store)
    result = await _execute(
        tool,
        {
            "title": "构建 API 层",
            "goal": "完成 /chat SSE 端点",
            "priority": "high",
            "steps": [
                {"title": "设计端点"},
                {"title": "实现 SSE", "note": "复用事件队列"},
            ],
        }
    )
    assert result["title"] == "构建 API 层"
    assert result["goal"] == "完成 /chat SSE 端点"
    assert result["priority"] == "high"
    assert result["status"] == "pending"
    assert len(result["steps"]) == 2
    assert result["steps"][0]["title"] == "设计端点"
    assert result["steps"][0]["status"] == "todo"

    loaded = await store.get(result["id"])
    assert loaded is not None and loaded.title == "构建 API 层"
    # 任务以 JSON 文件落盘。
    assert (store.tasks_dir / f"{result['id']}.json").is_file()


async def test_task_create_requires_title(store: FileTaskStore) -> None:
    tool = TaskCreateTool(store)
    with pytest.raises(ValueError, match="title"):
        await _execute(tool, {})


async def test_task_create_automatically_binds_execution_context(
    store: FileTaskStore,
) -> None:
    registry = _registry(store)
    call = ToolCall(
        id="create-1",
        name="task_create",
        arguments={"title": "长任务", "goal": "完成全部步骤"},
    )

    result = await ToolExecutor(registry).execute(
        call,
        context=ToolExecutionContext(
            tool_call=call,
            conversation_id="conv-current",
            run_id="run-current",
        ),
    )

    assert result.success is True
    tasks = await store.list()
    assert len(tasks) == 1
    assert tasks[0].owner_conversation_id == "conv-current"
    assert tasks[0].run_ids == ("run-current",)


async def test_task_update_advances_step(store: FileTaskStore) -> None:
    tool = TaskUpdateTool(store)
    created = await store.create(
        owner_conversation_id=_OWNER,
        title="任务",
        steps=(
            TaskStep(id="s1", title="步骤一"),
            TaskStep(id="s2", title="步骤二"),
        ),
    )
    result = await _execute(
        tool,
        {
            "task_id": created.id,
            "step_id": "s1",
            "step_status": "done",
            "step_note": "已完成",
        }
    )
    step = next(step for step in result["steps"] if step["id"] == "s1")
    assert step["status"] == "done"
    assert step["note"] == "已完成"


async def test_task_update_done_requires_note(store: FileTaskStore) -> None:
    """步骤标记 done 时必须提供 step_note 作为完成依据。"""

    tool = TaskUpdateTool(store)
    created = await store.create(
        owner_conversation_id=_OWNER,
        title="完成依据",
        steps=(TaskStep(id="s1", title="步骤一"),),
    )

    with pytest.raises(ValueError, match="step_note"):
        await _execute(
            tool,
            {
                "task_id": created.id,
                "step_id": "s1",
                "step_status": "done",
            }
        )
    # 空字符串同样不算依据。
    with pytest.raises(ValueError, match="step_note"):
        await _execute(
            tool,
            {
                "task_id": created.id,
                "step_id": "s1",
                "step_status": "done",
                "step_note": "   ",
            }
        )

    # 任务未被修改。
    unchanged = await store.get(created.id)
    assert unchanged is not None
    assert unchanged.steps[0].status is TaskStepStatus.TODO


async def test_task_update_in_progress_without_note_allowed(
    store: FileTaskStore,
) -> None:
    """非 done 状态（如 in_progress）不强制要求 step_note。"""

    tool = TaskUpdateTool(store)
    created = await store.create(
        owner_conversation_id=_OWNER,
        title="执行中",
        steps=(TaskStep(id="s1", title="步骤一"),),
    )

    result = await _execute(
        tool,
        {
            "task_id": created.id,
            "step_id": "s1",
            "step_status": "in_progress",
        }
    )
    assert result["steps"][0]["status"] == "in_progress"


async def test_task_update_blocked_requires_note(store: FileTaskStore) -> None:
    """步骤标记 blocked 时必须提供 step_note 说明阻塞原因。"""

    tool = TaskUpdateTool(store)
    created = await store.create(
        owner_conversation_id=_OWNER,
        title="阻塞原因",
        steps=(TaskStep(id="s1", title="步骤一"),),
    )

    with pytest.raises(ValueError, match="blocked"):
        await _execute(
            tool,
            {
                "task_id": created.id,
                "step_id": "s1",
                "step_status": "blocked",
            }
        )
    with pytest.raises(ValueError, match="blocked"):
        await _execute(
            tool,
            {
                "task_id": created.id,
                "step_id": "s1",
                "step_status": "blocked",
                "step_note": "  ",
            }
        )

    # 提供阻塞原因后可以成功。
    result = await _execute(
        tool,
        {
            "task_id": created.id,
            "step_id": "s1",
            "step_status": "blocked",
            "step_note": "缺少用户提供的实验结果文件",
        }
    )
    step = result["steps"][0]
    assert step["status"] == "blocked"
    assert step["note"] == "缺少用户提供的实验结果文件"


async def test_task_update_can_pause_and_resume(store: FileTaskStore) -> None:
    """任务可进入 paused（等待外部输入），恢复时仍可继续。"""

    tool = TaskUpdateTool(store)
    created = await store.create(
        title="等待输入", owner_conversation_id=_OWNER
    )

    paused = await _execute(
        tool, {"task_id": created.id, "status": "paused"}
    )
    assert paused["status"] == "paused"

    resumed = await _execute(
        tool, {"task_id": created.id, "status": "active"}
    )
    assert resumed["status"] == "active"


async def test_task_update_can_replace_plan_and_preserve_existing_step_id(
    store: FileTaskStore,
) -> None:
    tool = TaskUpdateTool(store)
    created = await store.create(
        owner_conversation_id=_OWNER,
        title="动态计划",
        steps=(TaskStep(id="existing", title="原步骤"),),
    )

    result = await _execute(
        tool,
        {
            "task_id": created.id,
            "expected_revision": created.revision,
            "steps": [
                {
                    "id": "existing",
                    "title": "调整后的步骤",
                    "status": "in_progress",
                },
                {"title": "新增步骤"},
            ],
        }
    )

    assert result["revision"] == 2
    assert result["steps"][0]["id"] == "existing"
    assert result["steps"][0]["status"] == "in_progress"
    assert len(result["steps"][1]["id"]) == 32


async def test_task_update_status_goal_state_constraints_facts(
    store: FileTaskStore,
) -> None:
    tool = TaskUpdateTool(store)
    created = await store.create(
        title="更新测试", owner_conversation_id=_OWNER
    )
    result = await _execute(
        tool,
        {
            "task_id": created.id,
            "status": "active",
            "goal": "新目标",
            "state": ["设计完成", "开始实现"],
            "constraints": ["只读", "安全优先"],
            "facts": ["使用 pydantic v2"],
        }
    )
    assert result["status"] == "active"
    assert result["goal"] == "新目标"
    assert result["state"] == ["设计完成", "开始实现"]
    assert result["constraints"] == ["只读", "安全优先"]
    assert result["key_facts"] == ["使用 pydantic v2"]


async def test_task_update_attaches_run_and_conversation(
    store: FileTaskStore,
) -> None:
    registry = _registry(store)
    # 任务已属于 conv-1（例如由 task_create 在 conv-1 中创建）。
    created = await store.create(
        title="关联", owner_conversation_id="conv-1"
    )
    call = ToolCall(
        id="update-1",
        name="task_update",
        arguments={"task_id": created.id, "status": "active"},
    )
    executed = await ToolExecutor(registry).execute(
        call,
        context=ToolExecutionContext(
            tool_call=call,
            conversation_id="conv-1",
            run_id="run-1",
        ),
    )
    assert executed.success is True
    updated = await store.get(created.id)
    assert updated is not None
    result = updated.model_dump(mode="json")
    assert result["owner_conversation_id"] == "conv-1"
    assert result["run_ids"] == ["run-1"]


async def test_task_update_requires_update_field(store: FileTaskStore) -> None:
    tool = TaskUpdateTool(store)
    created = await store.create(title="无更新", owner_conversation_id=_OWNER)
    with pytest.raises(ValueError, match="update field"):
        await _execute(tool, {"task_id": created.id})


async def test_task_update_step_requires_pair(store: FileTaskStore) -> None:
    tool = TaskUpdateTool(store)
    created = await store.create(title="成对校验", owner_conversation_id=_OWNER)
    with pytest.raises(ValueError, match="together"):
        await _execute(tool, {"task_id": created.id, "step_id": "s1"})


async def test_task_update_is_atomic_when_later_field_is_invalid(
    store: FileTaskStore,
) -> None:
    tool = TaskUpdateTool(store)
    created = await store.create(
        owner_conversation_id=_OWNER,
        title="原子更新",
        steps=(TaskStep(id="s1", title="步骤一"),),
    )

    with pytest.raises(ValueError, match="state"):
        await _execute(
            tool,
            {
                "task_id": created.id,
                "step_id": "s1",
                "step_status": "done",
                "state": 123,
            }
        )

    unchanged = await store.get(created.id)
    assert unchanged == created


async def test_task_update_missing_task(store: FileTaskStore) -> None:
    tool = TaskUpdateTool(store)
    with pytest.raises(KeyError, match="任务不存在"):
        await _execute(tool, {"task_id": "0" * 32, "goal": "x"})


async def test_task_get_returns_full_details(store: FileTaskStore) -> None:
    tool = TaskGetTool(store)
    created = await store.create(
        owner_conversation_id=_OWNER,
        title="详情",
        steps=(TaskStep(id="s1", title="步骤一"),),
        goal="目标",
    )
    result = await _execute(tool, {"task_id": created.id})
    assert result["id"] == created.id
    assert result["goal"] == "目标"
    assert result["steps"][0]["title"] == "步骤一"


async def test_task_get_missing(store: FileTaskStore) -> None:
    tool = TaskGetTool(store)
    with pytest.raises(KeyError, match="任务不存在"):
        await _execute(tool, {"task_id": "0" * 32})


async def test_task_list_filters_and_briefs(store: FileTaskStore) -> None:
    tool = TaskListTool(store)
    active = await store.create(title="进行中", owner_conversation_id=_OWNER)
    await store.set_status(active.id, TaskStatus.ACTIVE)
    await store.create(title="待办", owner_conversation_id=_OWNER)

    all_result = await _execute(tool, {})
    assert all_result["count"] == 2
    briefs = {item["title"]: item for item in all_result["tasks"]}
    assert set(briefs) == {"进行中", "待办"}
    assert "steps" not in all_result["tasks"][0]
    assert briefs["进行中"]["progress"].startswith("[active]")

    active_result = await _execute(tool, {"status": "active"})
    assert [item["title"] for item in active_result["tasks"]] == ["进行中"]


async def test_task_list_invalid_limit(store: FileTaskStore) -> None:
    tool = TaskListTool(store)
    with pytest.raises(ValueError, match="limit"):
        await _execute(tool, {"limit": 0})


async def test_task_list_scoped_to_current_conversation(
    store: FileTaskStore,
) -> None:
    """task_list 只返回当前会话的任务。"""

    await store.create(title="A 会话任务", owner_conversation_id="conv-a")
    await store.create(title="B 会话任务", owner_conversation_id="conv-b")
    tool = TaskListTool(store)

    result_a = await tool.execute_with_context(
        {},
        ToolExecutionContext(
            tool_call=ToolCall(id="list-1", name="task_list", arguments={}),
            conversation_id="conv-a",
            run_id="run-a",
        ),
    )
    assert [item["title"] for item in result_a["tasks"]] == ["A 会话任务"]

    result_b = await tool.execute_with_context(
        {},
        ToolExecutionContext(
            tool_call=ToolCall(id="list-2", name="task_list", arguments={}),
            conversation_id="conv-b",
            run_id="run-b",
        ),
    )
    assert [item["title"] for item in result_b["tasks"]] == ["B 会话任务"]


async def test_task_get_hidden_from_other_conversation(
    store: FileTaskStore,
) -> None:
    """其他会话无法获取不属于自己的任务（隐藏存在性）。"""

    created = await store.create(
        title="A 会话任务", owner_conversation_id="conv-a"
    )
    tool = TaskGetTool(store)

    with pytest.raises(KeyError, match="任务不存在"):
        await tool.execute_with_context(
            {"task_id": created.id},
            ToolExecutionContext(
                tool_call=ToolCall(id="get-1", name="task_get", arguments={}),
                conversation_id="conv-b",
                run_id="run-b",
            ),
        )

    result = await tool.execute_with_context(
        {"task_id": created.id},
        ToolExecutionContext(
            tool_call=ToolCall(id="get-2", name="task_get", arguments={}),
            conversation_id="conv-a",
            run_id="run-a",
        ),
    )
    assert result["id"] == created.id


async def test_task_update_rejected_from_other_conversation(
    store: FileTaskStore,
) -> None:
    """其他会话不能更新不属于自己的任务。"""

    created = await store.create(
        title="A 会话任务", owner_conversation_id="conv-a"
    )
    tool = TaskUpdateTool(store)

    with pytest.raises(KeyError, match="任务不存在"):
        await tool.execute_with_context(
            {"task_id": created.id, "status": "completed"},
            ToolExecutionContext(
                tool_call=ToolCall(id="upd-1", name="task_update", arguments={}),
                conversation_id="conv-b",
                run_id="run-b",
            ),
        )

    unchanged = await store.get(created.id)
    assert unchanged is not None and unchanged.status is TaskStatus.PENDING


async def test_executor_path_rejects_other_conversation_update(
    store: FileTaskStore,
) -> None:
    """即使其他会话知道任务 ID，执行器也会拒绝更新。"""

    registry = _registry(store)
    created = await store.create(title="A 任务", owner_conversation_id="conv-a")
    call = ToolCall(
        id="upd-exec-1",
        name="task_update",
        arguments={"task_id": created.id, "status": "completed"},
    )

    executed = await ToolExecutor(registry).execute(
        call,
        context=ToolExecutionContext(
            tool_call=call,
            conversation_id="conv-b",
            run_id="run-b",
        ),
    )

    assert executed.success is False
    assert "任务不存在" in (executed.error or "")
    unchanged = await store.get(created.id)
    assert unchanged is not None and unchanged.status is TaskStatus.PENDING


@pytest.mark.parametrize(
    ("tool_factory", "arguments"),
    [
        (TaskCreateTool, {"title": "无上下文"}),
        (TaskUpdateTool, {"task_id": "0" * 32, "goal": "目标"}),
        (TaskGetTool, {"task_id": "0" * 32}),
        (TaskListTool, {}),
    ],
)
async def test_task_tools_reject_missing_conversation_context(
    store: FileTaskStore,
    tool_factory,
    arguments: dict,
) -> None:
    """模型任务工具没有真实会话上下文时一律拒绝执行。"""

    tool = tool_factory(store)
    with pytest.raises(ValueError, match="conversation context"):
        await tool.execute(arguments)


async def test_same_prefix_in_other_conversation_does_not_cause_ambiguity(
    store: FileTaskStore,
) -> None:
    """前缀唯一性只在当前会话的任务集合中判断。"""

    common = "abcd1234"
    first = await store.create(
        title="A 任务",
        owner_conversation_id="conv-a",
    )
    second = await store.create(
        title="B 任务",
        owner_conversation_id="conv-b",
    )
    first_path = store.tasks_dir / f"{first.id}.json"
    second_path = store.tasks_dir / f"{second.id}.json"
    first_data = first.model_dump(mode="json")
    second_data = second.model_dump(mode="json")
    first_data["id"] = f"{common}aa".ljust(32, "0")
    second_data["id"] = f"{common}bb".ljust(32, "0")
    first_path.unlink()
    second_path.unlink()
    (store.tasks_dir / f"{first_data['id']}.json").write_text(
        json.dumps(first_data, ensure_ascii=False),
        encoding="utf-8",
    )
    (store.tasks_dir / f"{second_data['id']}.json").write_text(
        json.dumps(second_data, ensure_ascii=False),
        encoding="utf-8",
    )

    tool = TaskGetTool(store)
    result_a = await _execute(tool, {"task_id": common}, conversation_id="conv-a")
    result_b = await _execute(tool, {"task_id": common}, conversation_id="conv-b")
    assert result_a["id"] == first_data["id"]
    assert result_b["id"] == second_data["id"]


async def test_task_update_rejects_plan_and_single_step_together(
    store: FileTaskStore,
) -> None:
    task = await store.create(
        title="互斥更新",
        owner_conversation_id=_OWNER,
        steps=(TaskStep(id="s1", title="步骤"),),
    )
    path = store.tasks_dir / f"{task.id}.json"
    original = path.read_bytes()

    with pytest.raises(ValueError, match="cannot be combined"):
        await _execute(
            TaskUpdateTool(store),
            {
                "task_id": task.id,
                "steps": [{"id": "s1", "title": "步骤"}],
                "step_id": "s1",
                "step_status": "in_progress",
            },
        )
    assert path.read_bytes() == original
