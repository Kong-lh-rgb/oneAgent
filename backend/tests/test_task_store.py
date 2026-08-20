"""任务领域模型与文件系统存储测试。"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest

from app.task import (
    DEFAULT_TASKS_DIR,
    FileTaskStore,
    TaskContextProvider,
    TaskPatch,
    TaskPriority,
    TaskStatus,
    TaskStep,
    TaskStepStatus,
)

_OWNER = "conv-test"


@pytest.fixture
async def store(tmp_path) -> FileTaskStore:
    instance = FileTaskStore(tmp_path / "tasks")
    await instance.initialize()
    return instance


async def test_default_tasks_dir_under_vesta() -> None:
    assert DEFAULT_TASKS_DIR.name == "tasks"
    assert DEFAULT_TASKS_DIR.parent.name == ".vesta"


async def test_create_and_get_round_trip(store: FileTaskStore) -> None:
    task = await store.create(
        owner_conversation_id=_OWNER,
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
    task = await store.create(title="可读性", owner_conversation_id=_OWNER)
    task_file = store.tasks_dir / f"{task.id}.json"
    content = task_file.read_text(encoding="utf-8")
    assert "\n  " in content  # 缩进格式便于人工查看


async def test_normalizes_whitespace_and_deduplicates(store: FileTaskStore) -> None:
    task = await store.create(
        owner_conversation_id=_OWNER,
        title="  压缩  目标  ",
        goal="  完成  压缩  ",
    )
    assert task.title == "压缩 目标"
    assert task.goal == "完成 压缩"

    updated = await store.add_constraints(task.id, "  只读  ", "只读", "安全优先")
    assert updated.constraints == ("只读", "安全优先")


async def test_resolve_by_id_prefix(store: FileTaskStore) -> None:
    task = await store.create(title="前缀测试", owner_conversation_id=_OWNER)
    resolved = await store.resolve(
        task.id[:8], owner_conversation_id=_OWNER
    )
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
                    "owner_conversation_id": _OWNER,
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
        await store.resolve(common, owner_conversation_id=_OWNER)


async def test_status_lifecycle_sets_and_clears_completed_at(
    store: FileTaskStore,
) -> None:
    task = await store.create(title="生命周期", owner_conversation_id=_OWNER)
    active = await store.set_status(task.id, TaskStatus.ACTIVE)
    assert active.completed_at is None

    completed = await store.set_status(task.id, TaskStatus.COMPLETED)
    assert completed.status is TaskStatus.COMPLETED
    assert completed.completed_at is not None

    with pytest.raises(ValueError, match="terminal task"):
        await store.set_status(task.id, TaskStatus.ACTIVE)


async def test_step_status_advance(store: FileTaskStore) -> None:
    task = await store.create(
        owner_conversation_id=_OWNER,
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
        await store.set_step_status(
            task.id,
            "missing",
            TaskStepStatus.DONE,
            note="不存在",
        )


async def test_replace_steps(store: FileTaskStore) -> None:
    task = await store.create(title="重排步骤", owner_conversation_id=_OWNER)
    updated = await store.replace_steps(
        task.id,
        (
            TaskStep(
                id="x",
                title="新步骤",
                status=TaskStepStatus.DONE,
                note="已经完成",
            ),
        ),
    )
    assert [step.id for step in updated.steps] == ["x"]
    assert updated.steps[0].status is TaskStepStatus.DONE


async def test_update_goal_state_and_key_facts(store: FileTaskStore) -> None:
    task = await store.create(title="目标更新", owner_conversation_id=_OWNER)
    goal_updated = await store.update_goal(task.id, "新目标")
    assert goal_updated.goal == "新目标"

    state_updated = await store.update_state(task.id, "设计完成", "开始实现")
    assert state_updated.state == ("设计完成", "开始实现")

    facts_updated = await store.add_key_facts(task.id, "使用 pydantic v2")
    assert facts_updated.key_facts == ("使用 pydantic v2",)


async def test_owner_is_fixed_and_run_can_be_attached(store: FileTaskStore) -> None:
    task = await store.create(title="关联", owner_conversation_id="conv-1")
    with_run = await store.attach_run(task.id, "run-1")
    assert with_run.owner_conversation_id == "conv-1"
    assert with_run.run_ids == ("run-1",)

    duplicated = await store.attach_run(with_run.id, "run-1")
    assert duplicated.run_ids == ("run-1",)


async def test_list_filters_by_status_and_orders_by_update(
    store: FileTaskStore,
) -> None:
    pending = await store.create(title="待办", owner_conversation_id=_OWNER)
    in_progress = await store.create(
        title="进行中", owner_conversation_id=_OWNER
    )
    await store.set_status(in_progress.id, TaskStatus.ACTIVE)
    active = await store.create(title="进行中二", owner_conversation_id=_OWNER)
    await store.set_status(active.id, TaskStatus.ACTIVE)
    completed = await store.create(title="已完成", owner_conversation_id=_OWNER)
    await store.set_status(completed.id, TaskStatus.COMPLETED)

    actives = await store.list(status=TaskStatus.ACTIVE)
    assert {task.title for task in actives} == {"进行中", "进行中二"}

    all_tasks = await store.list(limit=10)
    assert all_tasks[0].title == "已完成"  # 最近更新在前
    assert pending.id in {task.id for task in all_tasks}


async def test_list_filters_by_conversation(store: FileTaskStore) -> None:
    task_a = await store.create(
        title="A 任务", owner_conversation_id="conv-a"
    )
    task_b = await store.create(
        title="B 任务", owner_conversation_id="conv-b"
    )

    in_a = await store.list(owner_conversation_id="conv-a")
    assert [task.id for task in in_a] == [task_a.id]

    in_b = await store.list(owner_conversation_id="conv-b")
    assert [task.id for task in in_b] == [task_b.id]

    all_tasks = await store.list(limit=10)
    assert {task.id for task in all_tasks} == {
        task_a.id,
        task_b.id,
    }


async def test_list_skips_corrupt_files(store: FileTaskStore) -> None:
    await store.create(title="正常任务", owner_conversation_id=_OWNER)
    corrupt = store.tasks_dir / "corrupt.json"
    corrupt.write_text("{ not valid json", encoding="utf-8")

    tasks = await store.list(limit=10)
    assert [task.title for task in tasks] == ["正常任务"]


async def test_delete(store: FileTaskStore) -> None:
    task = await store.create(title="删除", owner_conversation_id=_OWNER)
    assert await store.delete(task.id) is True
    assert await store.get(task.id) is None
    assert await store.delete(task.id) is False


async def test_missing_task_raises_key_error(store: FileTaskStore) -> None:
    with pytest.raises(KeyError, match="任务不存在"):
        await store.update_goal("0" * 32, "x")


async def test_progress_summary(store: FileTaskStore) -> None:
    task = await store.create(
        owner_conversation_id=_OWNER,
        title="进度",
        steps=(
            TaskStep(
                id="a",
                title="A",
                status=TaskStepStatus.DONE,
                note="完成",
            ),
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
    task = await store.create(title="外部文件", owner_conversation_id=_OWNER)
    task_file = store.tasks_dir / f"{task.id}.json"
    external = tmp_path / "external.json"
    task_file.replace(external)
    task_file.symlink_to(external)

    assert await store.get(task.id) is None


async def test_concurrent_updates_do_not_lose_facts(store: FileTaskStore) -> None:
    task = await store.create(title="并发更新", owner_conversation_id=_OWNER)

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
    task = await store.create(title="版本检查", owner_conversation_id=_OWNER)
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


async def test_active_task_is_latest_active_or_paused_for_conversation(
    store: FileTaskStore,
) -> None:
    first = await store.create(
        title="较早任务",
        owner_conversation_id="conv-1",
    )
    second = await store.create(
        title="当前任务",
        owner_conversation_id="conv-1",
    )
    await store.set_status(first.id, TaskStatus.COMPLETED)

    # 两个任务都还是 PENDING：不算 active task。
    assert await store.active_for_conversation("conv-1") is None

    # 用户接受第二个 → ACTIVE 成为活动任务。
    await store.plan_accept(second.id)
    active = await store.active_for_conversation("conv-1")
    assert active is not None
    assert active.id == second.id
    assert active.status is TaskStatus.ACTIVE

    # PENDING 计划（未接受）不能被当作正在执行的任务。
    pending_only = await store.create(
        title="未接受计划",
        owner_conversation_id="conv-2",
    )
    assert pending_only.status is TaskStatus.PENDING
    assert await store.active_for_conversation("conv-2") is None


async def test_task_context_provider_reads_only_current_owner(
    store: FileTaskStore,
) -> None:
    task_a = await store.create(
        title="A 私有任务",
        owner_conversation_id="conv-a",
    )
    task_b = await store.create(
        title="B 私有任务",
        owner_conversation_id="conv-b",
    )
    await store.plan_accept(task_a.id)
    await store.plan_accept(task_b.id)
    provider = TaskContextProvider(store)

    message_a = await provider.message_for("conv-a")
    message_b = await provider.message_for("conv-b")

    assert message_a is not None and task_a.id in (message_a.content or "")
    assert task_b.id not in (message_a.content or "")
    assert message_b is not None and task_b.id in (message_b.content or "")
    assert task_a.id not in (message_b.content or "")
    assert await provider.message_for(None) is None


async def test_pending_task_not_injected_as_active_context(
    store: FileTaskStore,
) -> None:
    """PENDING 计划不被当作正在执行的活动任务注入 Normal Mode 上下文。"""

    task = await store.create(
        title="未接受计划",
        owner_conversation_id="conv-a",
    )
    assert task.status is TaskStatus.PENDING
    provider = TaskContextProvider(store)

    assert await provider.message_for("conv-a") is None

    # 接受后才会被注入。
    await store.plan_accept(task.id)
    message = await provider.message_for("conv-a")
    assert message is not None and task.id in (message.content or "")


async def test_task_context_folds_old_done_steps_but_keeps_working_steps(
    store: FileTaskStore,
) -> None:
    task = await store.create(
        title="长任务",
        owner_conversation_id="conv-a",
        steps=tuple(
            TaskStep(
                id=f"step-{index}",
                title=f"步骤 {index}",
                status=TaskStepStatus.DONE,
                note=f"证据 {index}",
            )
            for index in range(5)
        )
        + (TaskStep(id="step-next", title="下一步"),),
    )
    await store.plan_accept(task.id)  # 只有 ACTIVE 任务才作为活动任务注入
    provider = TaskContextProvider(store, recent_done_steps=2)

    message = await provider.message_for("conv-a")

    assert message is not None
    content = message.content or ""
    assert '"omitted_done_steps":3' in content
    assert '"omitted_pending_steps":0' in content
    assert '"step-3"' in content
    assert '"step-4"' in content
    assert '"step-next"' in content
    assert '"step-0"' not in content
    assert "in_progress" in content
    assert task.id in content


async def test_resolve_prefix_filters_owner_before_ambiguity(
    store: FileTaskStore,
) -> None:
    """不同会话共享相同前缀时，各自仍能唯一解析自己的任务。"""

    common = "abcd1234"
    task_ids = (
        f"{common}aa".ljust(32, "0"),
        f"{common}bb".ljust(32, "0"),
    )
    for task_id, owner in zip(task_ids, ("conv-a", "conv-b"), strict=True):
        _write_task_payload(
            store,
            task_id=task_id,
            owner_conversation_id=owner,
        )

    resolved_a = await store.resolve(
        common,
        owner_conversation_id="conv-a",
    )
    resolved_b = await store.resolve(
        common,
        owner_conversation_id="conv-b",
    )
    assert resolved_a is not None and resolved_a.id == task_ids[0]
    assert resolved_b is not None and resolved_b.id == task_ids[1]

    with pytest.raises(ValueError, match="前缀不唯一"):
        await store.resolve(common)


async def test_legacy_single_owner_is_migrated_atomically(
    store: FileTaskStore,
) -> None:
    task_id = "a" * 32
    path = _write_task_payload(
        store,
        task_id=task_id,
        conversation_ids=["conv-legacy"],
    )

    loaded = await store.get(task_id)

    assert loaded is not None
    assert loaded.owner_conversation_id == "conv-legacy"
    migrated = json.loads(path.read_text(encoding="utf-8"))
    assert migrated["owner_conversation_id"] == "conv-legacy"
    assert "conversation_ids" not in migrated
    assert migrated["revision"] == 1


@pytest.mark.parametrize("legacy_owners", [[], ["conv-a", "conv-b"]])
async def test_legacy_ambiguous_owner_warns_and_is_inaccessible(
    store: FileTaskStore,
    caplog,
    legacy_owners: list[str],
) -> None:
    task_id = "b" * 32
    path = _write_task_payload(
        store,
        task_id=task_id,
        conversation_ids=legacy_owners,
    )
    original = path.read_bytes()

    assert await store.get(task_id) is None
    assert await store.resolve(
        task_id,
        owner_conversation_id="conv-a",
    ) is None
    assert await store.list(owner_conversation_id="conv-a") == ()
    assert path.read_bytes() == original
    assert "ambiguous owner" in caplog.text


async def test_task_rejects_multiple_in_progress_steps(
    store: FileTaskStore,
) -> None:
    with pytest.raises(ValueError, match="at most one in_progress"):
        await store.create(
            title="校验",
            owner_conversation_id=_OWNER,
            steps=(
                TaskStep(id="a", title="A", status=TaskStepStatus.IN_PROGRESS),
                TaskStep(id="b", title="B", status=TaskStepStatus.IN_PROGRESS),
            )
        )
    assert list(store.tasks_dir.glob("*.json")) == []


@pytest.mark.parametrize("status", [TaskStepStatus.DONE, TaskStepStatus.BLOCKED])
def test_done_and_blocked_step_require_note(status: TaskStepStatus) -> None:
    with pytest.raises(ValueError, match="requires a note"):
        TaskStep(id="step", title="步骤", status=status)


async def test_paused_task_rejects_in_progress_step_without_writing(
    store: FileTaskStore,
) -> None:
    task = await store.create(
        title="暂停约束",
        owner_conversation_id=_OWNER,
        steps=(
            TaskStep(id="s1", title="执行", status=TaskStepStatus.IN_PROGRESS),
        ),
    )
    await _assert_patch_rejected_without_file_change(
        store,
        task.id,
        TaskPatch(status=TaskStatus.PAUSED),
        "paused task",
    )


async def test_completed_task_requires_every_step_done_without_writing(
    store: FileTaskStore,
) -> None:
    task = await store.create(
        title="完成约束",
        owner_conversation_id=_OWNER,
        steps=(TaskStep(id="s1", title="未完成"),),
    )
    await _assert_patch_rejected_without_file_change(
        store,
        task.id,
        TaskPatch(status=TaskStatus.COMPLETED),
        "all steps to be done",
    )


async def test_done_step_cannot_be_rolled_back_without_writing(
    store: FileTaskStore,
) -> None:
    task = await store.create(
        title="完成步骤不可回退",
        owner_conversation_id=_OWNER,
        steps=(
            TaskStep(
                id="s1",
                title="已完成",
                status=TaskStepStatus.DONE,
                note="通过测试",
            ),
        ),
    )
    await _assert_patch_rejected_without_file_change(
        store,
        task.id,
        TaskPatch(step_id="s1", step_status=TaskStepStatus.TODO),
        "cannot be rolled back",
    )


@pytest.mark.parametrize(
    ("initial_status", "replacement"),
    [
        (TaskStepStatus.DONE, ()),
        (
            TaskStepStatus.DONE,
            (TaskStep(id="s1", title="回退", status=TaskStepStatus.TODO),),
        ),
        (TaskStepStatus.IN_PROGRESS, ()),
        (
            TaskStepStatus.IN_PROGRESS,
            (TaskStep(id="s1", title="回退", status=TaskStepStatus.TODO),),
        ),
    ],
)
async def test_replace_steps_cannot_delete_or_rollback_started_steps(
    store: FileTaskStore,
    initial_status: TaskStepStatus,
    replacement: tuple[TaskStep, ...],
) -> None:
    note = "已经完成" if initial_status is TaskStepStatus.DONE else None
    task = await store.create(
        title="重排保护",
        owner_conversation_id=_OWNER,
        steps=(
            TaskStep(id="s1", title="受保护", status=initial_status, note=note),
        ),
    )
    await _assert_patch_rejected_without_file_change(
        store,
        task.id,
        TaskPatch(replace_steps=replacement),
        "replace_steps cannot",
    )


@pytest.mark.parametrize(
    "terminal_status",
    [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED],
)
async def test_terminal_task_cannot_be_reopened_without_writing(
    store: FileTaskStore,
    terminal_status: TaskStatus,
) -> None:
    task = await store.create(
        title="终态保护",
        owner_conversation_id=_OWNER,
    )
    terminal = await store.set_status(task.id, terminal_status)
    await _assert_patch_rejected_without_file_change(
        store,
        terminal.id,
        TaskPatch(status=TaskStatus.ACTIVE),
        "terminal task",
    )


async def test_replace_steps_and_single_step_update_are_mutually_exclusive(
    store: FileTaskStore,
) -> None:
    task = await store.create(
        title="更新互斥",
        owner_conversation_id=_OWNER,
        steps=(TaskStep(id="s1", title="步骤"),),
    )
    path = store.tasks_dir / f"{task.id}.json"
    original = path.read_bytes()

    with pytest.raises(ValueError, match="cannot be combined"):
        TaskPatch(
            replace_steps=task.steps,
            step_id="s1",
            step_status=TaskStepStatus.IN_PROGRESS,
        )
    assert path.read_bytes() == original


async def _assert_patch_rejected_without_file_change(
    store: FileTaskStore,
    task_id: str,
    patch: TaskPatch,
    message: str,
) -> None:
    path = store.tasks_dir / f"{task_id}.json"
    original = path.read_bytes()
    with pytest.raises(ValueError, match=message):
        await store.apply_patch(task_id, patch)
    assert path.read_bytes() == original


def _write_task_payload(
    store: FileTaskStore,
    *,
    task_id: str,
    owner_conversation_id: str | None = None,
    conversation_ids: list[str] | None = None,
):
    payload = {
        "id": task_id,
        "title": "兼容任务",
        "status": "pending",
        "priority": "normal",
        "constraints": [],
        "state": [],
        "key_facts": [],
        "steps": [],
        "run_ids": [],
        "created_at": datetime(2026, 8, 6, tzinfo=UTC).isoformat(),
        "updated_at": datetime(2026, 8, 6, tzinfo=UTC).isoformat(),
        "completed_at": None,
        "revision": 1,
    }
    if owner_conversation_id is not None:
        payload["owner_conversation_id"] = owner_conversation_id
    else:
        payload["conversation_ids"] = conversation_ids or []
    path = store.tasks_dir / f"{task_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path
