"""任务领域模型与文件系统存储测试。"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.task import (
    DEFAULT_TASKS_DIR,
    FileTaskStore,
    TaskPatch,
    TaskPriority,
    TaskStatus,
    TaskStep,
    TaskStepStatus,
)


@pytest.fixture
async def store(tmp_path) -> FileTaskStore:
    instance = FileTaskStore(tmp_path / "tasks")
    await instance.initialize()
    return instance


async def test_default_tasks_dir_under_oneagent() -> None:
    assert DEFAULT_TASKS_DIR.name == "tasks"
    assert DEFAULT_TASKS_DIR.parent.name == ".oneagent"


async def test_create_and_get_round_trip(store: FileTaskStore) -> None:
    task = await store.create(
        title="构建 API 层",
        description="把 Agent 暴露为可调用服务",
        goal="完成 /chat SSE 端点",
        priority=TaskPriority.HIGH,
        steps=(
            TaskStep(id="s1", title="设计端点"),
            TaskStep(id="s2", title="实现 SSE"),
        ),
    )

    loaded = await store.get(task.id)
    assert loaded is not None
    assert loaded.title == "构建 API 层"
    assert loaded.description == "把 Agent 暴露为可调用服务"
    assert loaded.goal == "完成 /chat SSE 端点"
    assert loaded.priority is TaskPriority.HIGH
    assert loaded.status is TaskStatus.PENDING
    assert len(loaded.steps) == 2
    assert loaded.completed_at is None

    # 每个任务保存为一个独立 JSON 文件。
    task_file = store.tasks_dir / f"{task.id}.json"
    assert task_file.is_file()
    payload = json.loads(task_file.read_text(encoding="utf-8"))
    assert payload["title"] == "构建 API 层"
    assert payload["steps"][0]["title"] == "设计端点"


async def test_create_writes_pretty_printed_json(store: FileTaskStore) -> None:
    task = await store.create(title="可读性")
    task_file = store.tasks_dir / f"{task.id}.json"
    content = task_file.read_text(encoding="utf-8")
    assert "\n  " in content  # 缩进格式便于人工查看


async def test_normalizes_whitespace_and_deduplicates(store: FileTaskStore) -> None:
    task = await store.create(
        title="  压缩  目标  ",
        goal="  完成  压缩  ",
    )
    assert task.title == "压缩 目标"
    assert task.goal == "完成 压缩"

    updated = await store.add_constraints(task.id, "  只读  ", "只读", "安全优先")
    assert updated.constraints == ("只读", "安全优先")


async def test_resolve_by_id_prefix(store: FileTaskStore) -> None:
    task = await store.create(title="前缀测试")
    resolved = await store.resolve(task.id[:8])
    assert resolved is not None and resolved.id == task.id


async def test_resolve_ambiguous_prefix_raises(store: FileTaskStore) -> None:
    """手动写入两个共享前缀 ID 的任务文件，验证歧义前缀抛出错误。"""

    common = "abcd1234"
    for suffix, title in (("aa", "任务 A"), ("bb", "任务 B")):
        task_id = f"{common}{suffix}".ljust(32, "0")
        path = store.tasks_dir / f"{task_id}.json"
        path.write_text(
            json.dumps(
                {
                    "id": task_id,
                    "title": title,
                    "status": "pending",
                    "priority": "normal",
                    "constraints": [],
                    "state": [],
                    "key_facts": [],
                    "steps": [],
                    "conversation_ids": [],
                    "run_ids": [],
                    "created_at": "2026-08-06T00:00:00+00:00",
                    "updated_at": "2026-08-06T00:00:00+00:00",
                    "completed_at": None,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    with pytest.raises(ValueError, match="前缀不唯一"):
        await store.resolve(common)


async def test_status_lifecycle_sets_and_clears_completed_at(
    store: FileTaskStore,
) -> None:
    task = await store.create(title="生命周期")
    active = await store.set_status(task.id, TaskStatus.ACTIVE)
    assert active.completed_at is None

    completed = await store.set_status(task.id, TaskStatus.COMPLETED)
    assert completed.status is TaskStatus.COMPLETED
    assert completed.completed_at is not None

    reopened = await store.set_status(task.id, TaskStatus.ACTIVE)
    assert reopened.completed_at is None


async def test_step_status_advance(store: FileTaskStore) -> None:
    task = await store.create(
        title="步骤推进",
        steps=(
            TaskStep(id="s1", title="步骤一"),
            TaskStep(id="s2", title="步骤二"),
        ),
    )

    updated = await store.set_step_status(
        task.id,
        "s1",
        TaskStepStatus.IN_PROGRESS,
        note="开始",
    )
    step = next(step for step in updated.steps if step.id == "s1")
    assert step.status is TaskStepStatus.IN_PROGRESS
    assert step.note == "开始"

    with pytest.raises(KeyError, match="步骤不存在"):
        await store.set_step_status(task.id, "missing", TaskStepStatus.DONE)


async def test_replace_steps(store: FileTaskStore) -> None:
    task = await store.create(title="重排步骤")
    updated = await store.replace_steps(
        task.id,
        (TaskStep(id="x", title="新步骤", status=TaskStepStatus.DONE),),
    )
    assert [step.id for step in updated.steps] == ["x"]
    assert updated.steps[0].status is TaskStepStatus.DONE


async def test_update_goal_state_and_key_facts(store: FileTaskStore) -> None:
    task = await store.create(title="目标更新")
    goal_updated = await store.update_goal(task.id, "新目标")
    assert goal_updated.goal == "新目标"

    state_updated = await store.update_state(task.id, "设计完成", "开始实现")
    assert state_updated.state == ("设计完成", "开始实现")

    facts_updated = await store.add_key_facts(task.id, "使用 pydantic v2")
    assert facts_updated.key_facts == ("使用 pydantic v2",)


async def test_attach_conversation_and_run(store: FileTaskStore) -> None:
    task = await store.create(title="关联")
    with_conv = await store.attach_conversation(task.id, "conv-1")
    with_run = await store.attach_run(with_conv.id, "run-1")
    assert with_run.conversation_ids == ("conv-1",)
    assert with_run.run_ids == ("run-1",)

    duplicated = await store.attach_run(with_run.id, "run-1")
    assert duplicated.run_ids == ("run-1",)


async def test_list_filters_by_status_and_orders_by_update(
    store: FileTaskStore,
) -> None:
    pending = await store.create(title="待办")
    in_progress = await store.create(title="进行中")
    await store.set_status(in_progress.id, TaskStatus.ACTIVE)
    active = await store.create(title="进行中二")
    await store.set_status(active.id, TaskStatus.ACTIVE)
    completed = await store.create(title="已完成")
    await store.set_status(completed.id, TaskStatus.COMPLETED)

    actives = await store.list(status=TaskStatus.ACTIVE)
    assert {task.title for task in actives} == {"进行中", "进行中二"}

    all_tasks = await store.list(limit=10)
    assert all_tasks[0].title == "已完成"  # 最近更新在前
    assert pending.id in {task.id for task in all_tasks}


async def test_list_filters_by_conversation(store: FileTaskStore) -> None:
    task_a = await store.create(title="A 任务", conversation_ids=("conv-a",))
    task_b = await store.create(title="B 任务", conversation_ids=("conv-b",))
    task_free = await store.create(title="无绑定")

    in_a = await store.list(conversation_id="conv-a")
    assert [task.id for task in in_a] == [task_a.id]

    in_b = await store.list(conversation_id="conv-b")
    assert [task.id for task in in_b] == [task_b.id]

    all_tasks = await store.list(limit=10)
    assert {task.id for task in all_tasks} == {
        task_a.id,
        task_b.id,
        task_free.id,
    }


async def test_list_skips_corrupt_files(store: FileTaskStore) -> None:
    await store.create(title="正常任务")
    corrupt = store.tasks_dir / "corrupt.json"
    corrupt.write_text("{ not valid json", encoding="utf-8")

    tasks = await store.list(limit=10)
    assert [task.title for task in tasks] == ["正常任务"]


async def test_delete(store: FileTaskStore) -> None:
    task = await store.create(title="删除")
    assert await store.delete(task.id) is True
    assert await store.get(task.id) is None
    assert await store.delete(task.id) is False


async def test_missing_task_raises_key_error(store: FileTaskStore) -> None:
    with pytest.raises(KeyError, match="任务不存在"):
        await store.update_goal("0" * 32, "x")


async def test_progress_summary(store: FileTaskStore) -> None:
    task = await store.create(
        title="进度",
        steps=(
            TaskStep(id="a", title="A", status=TaskStepStatus.DONE),
            TaskStep(id="b", title="B"),
        ),
    )
    assert task.progress_summary == "[pending] 进度 (1/2 步骤完成)"


async def test_rejects_path_traversal_and_absolute_identifiers(
    store: FileTaskStore,
) -> None:
    with pytest.raises(ValueError, match="task_id"):
        await store.get("../outside")
    with pytest.raises(ValueError, match="task_id"):
        await store.delete("/tmp/outside")
    with pytest.raises(ValueError, match="identifier"):
        await store.resolve("../../")


async def test_rejects_symlinked_task_file(store: FileTaskStore, tmp_path) -> None:
    task = await store.create(title="外部文件")
    task_file = store.tasks_dir / f"{task.id}.json"
    external = tmp_path / "external.json"
    task_file.replace(external)
    task_file.symlink_to(external)

    assert await store.get(task.id) is None


async def test_concurrent_updates_do_not_lose_facts(store: FileTaskStore) -> None:
    task = await store.create(title="并发更新")

    await asyncio.gather(
        store.add_key_facts(task.id, "事实 A"),
        store.add_key_facts(task.id, "事实 B"),
    )

    updated = await store.get(task.id)
    assert updated is not None
    assert set(updated.key_facts) == {"事实 A", "事实 B"}
    assert updated.revision == 3


async def test_revision_conflict_does_not_overwrite_task(
    store: FileTaskStore,
) -> None:
    task = await store.create(title="版本检查")
    updated = await store.apply_patch(
        task.id,
        TaskPatch(goal="第一版", expected_revision=1),
    )

    with pytest.raises(ValueError, match="revision conflict"):
        await store.apply_patch(
            task.id,
            TaskPatch(goal="过期覆盖", expected_revision=1),
        )

    current = await store.get(task.id)
    assert current == updated


async def test_active_task_is_latest_non_terminal_for_conversation(
    store: FileTaskStore,
) -> None:
    first = await store.create(
        title="较早任务",
        conversation_ids=("conv-1",),
    )
    second = await store.create(
        title="当前任务",
        conversation_ids=("conv-1",),
    )
    await store.set_status(first.id, TaskStatus.COMPLETED)

    active = await store.active_for_conversation("conv-1")

    assert active is not None
    assert active.id == second.id
