"""基于文件系统的任务持久化。

每个任务保存为 tasks 目录下的一个结构化 JSON 文件（``<id>.json``），
不写入 SQLite。任务事实独立于会话数据库，便于人工查看、备份与版本管理。

文件写入采用"临时文件 + 原子替换"，避免进程中断产生损坏文件；目录与文件
路径均可通过构造参数自定义。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .models import (
    TASK_ID_LENGTH,
    Task,
    TaskPatch,
    TaskPriority,
    TaskStatus,
    TaskStep,
    TaskStepStatus,
)

DEFAULT_TASKS_DIR = Path(__file__).resolve().parents[2] / ".vesta" / "tasks"
MAX_TASK_FILE_BYTES = 1_000_000
logger = logging.getLogger("vesta.task.store")

_TERMINAL_STATUSES = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
)
# 会被当作“正在执行的活动任务”注入模型上下文的非终态状态。
# PENDING（计划已生成但尚未开始执行）不算 active task —— 不应注入上下文。
_CONTEXT_TASK_STATUSES = frozenset({TaskStatus.ACTIVE, TaskStatus.PAUSED})
_TASK_ID_RE = re.compile(rf"^[0-9a-f]{{{TASK_ID_LENGTH}}}$")
_TASK_PREFIX_RE = re.compile(rf"^[0-9a-f]{{4,{TASK_ID_LENGTH}}}$")


class LegacyTaskOwnershipError(ValueError):
    """旧任务无法确定唯一 owner，因此不能暴露给模型。"""


class FileTaskStore:
    """任务的 CRUD、状态推进与关联管理（本地 JSON 文件存储）。"""

    def __init__(self, tasks_dir: str | Path = DEFAULT_TASKS_DIR) -> None:
        self.tasks_dir = Path(tasks_dir).expanduser().resolve()
        self._locks: dict[str, asyncio.Lock] = {}

    async def initialize(self) -> None:
        """创建任务目录。"""

        await asyncio.to_thread(
            self.tasks_dir.mkdir,
            parents=True,
            exist_ok=True,
        )

    async def create(
        self,
        *,
        title: str,
        description: str | None = None,
        goal: str | None = None,
        priority: TaskPriority = TaskPriority.NORMAL,
        steps: Sequence[TaskStep] = (),
        owner_conversation_id: str,
        run_ids: Sequence[str] = (),
    ) -> Task:
        """创建一个新任务。"""

        now = datetime.now(UTC)
        task = Task(
            id=uuid4().hex,
            title=title,
            description=description,
            goal=goal,
            status=TaskStatus.PENDING,
            priority=priority,
            steps=tuple(steps),
            owner_conversation_id=_normalize_required_entry(
                owner_conversation_id,
                field_name="owner_conversation_id",
            ),
            run_ids=_merge_entries((), run_ids),
            created_at=now,
            updated_at=now,
        )
        await self._write(task)
        return task

    async def get(self, task_id: str) -> Task | None:
        """按完整 ID 获取任务。"""

        normalized = _validate_task_id(task_id)
        path = self._path(normalized)
        if not await asyncio.to_thread(path.is_file):
            return None
        if await asyncio.to_thread(path.is_symlink):
            return None
        try:
            return await asyncio.to_thread(_read_task, path)
        except LegacyTaskOwnershipError:
            return None

    async def resolve(
        self,
        identifier: str,
        *,
        owner_conversation_id: str | None = None,
    ) -> Task | None:
        """使用完整 ID 或唯一前缀查找，可先按任务 owner 过滤。"""

        normalized = identifier.strip().lower()
        if not normalized:
            return None

        _validate_task_prefix(normalized)

        owner = (
            _normalize_required_entry(
                owner_conversation_id,
                field_name="owner_conversation_id",
            )
            if owner_conversation_id is not None
            else None
        )
        if len(normalized) == TASK_ID_LENGTH:
            exact = await self.get(normalized)
            if exact is not None and (
                owner is None or exact.owner_conversation_id == owner
            ):
                return exact
            return None

        matches = [
            task
            for task in await self._all_tasks()
            if task.id.startswith(normalized)
            and (owner is None or task.owner_conversation_id == owner)
        ]
        if len(matches) > 1:
            raise ValueError(f"任务 ID 前缀不唯一：{identifier}")
        return matches[0] if matches else None

    async def list(
        self,
        *,
        limit: int = 50,
        status: TaskStatus | None = None,
        owner_conversation_id: str | None = None,
    ) -> tuple[Task, ...]:
        """按最近更新时间倒序列出任务，可按状态与归属会话过滤。"""

        if limit < 1:
            raise ValueError("limit must be at least 1")
        tasks = await self._all_tasks()
        if status is not None:
            tasks = [task for task in tasks if task.status is status]
        if owner_conversation_id is not None:
            normalized = _normalize_required_entry(
                owner_conversation_id,
                field_name="owner_conversation_id",
            )
            tasks = [
                task
                for task in tasks
                if normalized == task.owner_conversation_id
            ]
        tasks.sort(key=lambda task: task.updated_at, reverse=True)
        return tuple(tasks[:limit])

    async def delete(self, task_id: str) -> bool:
        """删除任务文件并返回是否实际删除。"""

        normalized = _validate_task_id(task_id)
        async with self._lock_for(normalized):
            path = self._path(normalized)
            if not await asyncio.to_thread(path.is_file):
                return False
            if await asyncio.to_thread(path.is_symlink):
                return False
            await asyncio.to_thread(path.unlink)
            return True

    async def apply_patch(
        self,
        task_id: str,
        patch: TaskPatch,
        *,
        owner_conversation_id: str | None = None,
    ) -> Task:
        """原子应用一组任务变更；校验失败时不写入任何部分结果。"""

        normalized = _validate_task_id(task_id)
        if not patch.has_changes:
            raise ValueError("task patch must contain at least one change")
        async with self._lock_for(normalized):
            task = await self._require(normalized)
            if owner_conversation_id is not None:
                owner = _normalize_required_entry(
                    owner_conversation_id,
                    field_name="owner_conversation_id",
                )
                if task.owner_conversation_id != owner:
                    raise KeyError(f"任务不存在：{task_id}")
            if (
                patch.expected_revision is not None
                and patch.expected_revision != task.revision
            ):
                raise ValueError(
                    "task revision conflict: "
                    f"expected {patch.expected_revision}, current {task.revision}"
                )
            updated = _apply_patch(task, patch, datetime.now(UTC))
            await self._write(updated)
            return updated

    async def update_goal(self, task_id: str, goal: str | None) -> Task:
        """替换当前目标。"""

        return await self._update(
            task_id,
            TaskPatch(goal=goal),
        )

    async def update_state(self, task_id: str, *state: str) -> Task:
        """用最新状态事实替换旧状态。"""

        return await self._update(
            task_id,
            TaskPatch(state=tuple(state)),
        )

    async def add_constraints(self, task_id: str, *constraints: str) -> Task:
        """追加用户约束并去重。"""

        return await self._update(
            task_id,
            TaskPatch(add_constraints=tuple(constraints)),
        )

    async def add_key_facts(self, task_id: str, *facts: str) -> Task:
        """追加关键事实并去重。"""

        return await self._update(
            task_id,
            TaskPatch(add_key_facts=tuple(facts)),
        )

    async def replace_steps(
        self,
        task_id: str,
        steps: Sequence[TaskStep],
    ) -> Task:
        """整体替换任务步骤列表。"""

        return await self._update(
            task_id,
            TaskPatch(replace_steps=tuple(steps)),
        )

    async def set_step_status(
        self,
        task_id: str,
        step_id: str,
        status: TaskStepStatus,
        *,
        note: str | None = None,
    ) -> Task:
        """推进单个步骤的状态；步骤不存在时抛出 KeyError。"""

        return await self._update(
            task_id,
            TaskPatch(step_id=step_id, step_status=status, step_note=note),
        )

    async def set_status(self, task_id: str, status: TaskStatus) -> Task:
        """推进任务状态；进入/离开终态时维护 completed_at。"""

        return await self._update(task_id, TaskPatch(status=status))

    async def plan_accept(self, task_id: str) -> Task:
        """接受一个 PENDING 计划：PENDING → ACTIVE。

        只允许 PENDING 任务被接受；已 ACTIVE / COMPLETED / CANCELLED 的
        任务不接受（补状态校验）。
        """

        return await self._transition_from_pending(
            task_id,
            TaskStatus.ACTIVE,
        )

    async def plan_reject(self, task_id: str) -> Task:
        """拒绝一个 PENDING 计划：PENDING → CANCELLED。

        只允许 PENDING 任务被拒绝；已 ACTIVE / COMPLETED / CANCELLED 的
        任务不允许再次拒绝。
        """

        return await self._transition_from_pending(
            task_id,
            TaskStatus.CANCELLED,
        )

    async def _transition_from_pending(
        self,
        task_id: str,
        status: TaskStatus,
    ) -> Task:
        """在任务锁内原子地做“仅 PENDING 可转换”的校验与写入。"""

        normalized = _validate_task_id(task_id)
        async with self._lock_for(normalized):
            task = await self._require(normalized)
            if task.status is not TaskStatus.PENDING:
                raise ValueError(
                    f"only pending task can be transitioned: {task_id} "
                    f"({task.status.value})"
                )
            now = datetime.now(UTC)
            data = task.model_dump(mode="python")
            data["status"] = status
            if status in _TERMINAL_STATUSES:
                data["completed_at"] = now
            data["revision"] = task.revision + 1
            data["updated_at"] = now
            updated = Task.model_validate(data)
            await self._write(updated)
            return updated

    async def attach_run(self, task_id: str, run_id: str) -> Task:
        """把一次 Agent 运行关联到任务。"""

        return await self._update(
            task_id,
            TaskPatch(run_id=run_id),
        )

    async def active_for_conversation(
        self,
        conversation_id: str,
    ) -> Task | None:
        """返回会话最近更新的活动任务（ACTIVE / PAUSED）。

        PENDING 不算活动任务：计划已生成但尚未开始执行，不应作为“正在执行的
        任务”注入模型上下文（Plan Mode 产生的 PENDING 计划由用户接受后才算
        开始执行）。
        """

        normalized = _normalize_required_entry(
            conversation_id,
            field_name="conversation_id",
        )
        tasks = [
            task
            for task in await self._all_tasks()
            if normalized == task.owner_conversation_id
            and task.status in _CONTEXT_TASK_STATUSES
        ]
        tasks.sort(key=lambda task: task.updated_at, reverse=True)
        return tasks[0] if tasks else None

    async def _require(self, task_id: str) -> Task:
        task = await self.get(task_id)
        if task is None:
            raise KeyError(f"任务不存在：{task_id}")
        return task

    async def _update(
        self,
        task_id: str,
        patch: TaskPatch,
    ) -> Task:
        return await self.apply_patch(task_id, patch)

    async def _all_tasks(self) -> list[Task]:
        """读取目录下全部任务；跳过损坏或无法解析的文件。"""

        return await asyncio.to_thread(_scan_tasks, self.tasks_dir)

    async def _write(self, task: Task) -> None:
        path = self._path(task.id)
        if await asyncio.to_thread(path.is_symlink):
            raise ValueError("task path cannot be a symbolic link")
        await asyncio.to_thread(_write_task, path, task)

    def _path(self, task_id: str) -> Path:
        normalized = _validate_task_id(task_id)
        path = self.tasks_dir / f"{normalized}.json"
        if path.parent.resolve() != self.tasks_dir:
            raise ValueError("task path escapes tasks directory")
        return path

    def _lock_for(self, task_id: str) -> asyncio.Lock:
        lock = self._locks.get(task_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[task_id] = lock
        return lock


def _scan_tasks(tasks_dir: Path) -> list[Task]:
    if not tasks_dir.is_dir():
        return []
    tasks: list[Task] = []
    for path in sorted(tasks_dir.glob("*.json")):
        if path.is_symlink() or not _TASK_ID_RE.fullmatch(path.stem):
            continue
        try:
            task = _read_task(path)
            if task.id != path.stem:
                continue
            tasks.append(task)
        except LegacyTaskOwnershipError:
            # _read_task 已记录明确的归属告警，此处只跳过不可访问任务。
            continue
        except (OSError, ValueError, TypeError) as exc:
            logger.warning(
                "Skipping invalid task file path=%s error=%s: %s",
                path,
                type(exc).__name__,
                exc,
            )
            continue
    return tasks


def _read_task(path: Path) -> Task:
    if path.stat().st_size > MAX_TASK_FILE_BYTES:
        raise ValueError(
            f"task file exceeds {MAX_TASK_FILE_BYTES} byte safety limit"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    if "owner_conversation_id" not in data:
        legacy_owners = data.pop("conversation_ids", None)
        if not isinstance(legacy_owners, list) or len(legacy_owners) != 1:
            logger.warning(
                "Legacy task has ambiguous owner and is inaccessible path=%s owners=%r",
                path,
                legacy_owners,
            )
            raise LegacyTaskOwnershipError(
                "legacy task must have exactly one conversation owner"
            )
        data["owner_conversation_id"] = legacy_owners[0]
        task = Task.model_validate(data)
        _write_task(path, task)
        logger.info(
            "Migrated legacy task owner path=%s owner_conversation_id=%s",
            path,
            task.owner_conversation_id,
        )
        return task
    if "conversation_ids" in data:
        raise ValueError("task cannot contain both owner ownership schemas")
    return Task.model_validate(data)


def _write_task(path: Path, task: Task) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        task.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
    )
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _apply_patch(task: Task, patch: TaskPatch, now: datetime) -> Task:
    """在内存中完成整组变更，最后统一通过 Task 重新校验。"""

    data = task.model_dump(mode="python")
    if task.status in _TERMINAL_STATUSES:
        if patch.status is not None and patch.status is not task.status:
            raise ValueError("terminal task cannot be reopened by ordinary update")
    if "goal" in patch.model_fields_set:
        data["goal"] = patch.goal
    if patch.status is not None:
        data["status"] = patch.status
        if patch.status in _TERMINAL_STATUSES:
            if task.status not in _TERMINAL_STATUSES:
                data["completed_at"] = now
        elif task.status in _TERMINAL_STATUSES:
            data["completed_at"] = None
    if "state" in patch.model_fields_set:
        data["state"] = patch.state or ()
    if patch.add_constraints:
        data["constraints"] = _merge_entries(
            task.constraints,
            patch.add_constraints,
        )
    if patch.add_key_facts:
        data["key_facts"] = _merge_entries(
            task.key_facts,
            patch.add_key_facts,
        )
    if "replace_steps" in patch.model_fields_set:
        replacement_steps = patch.replace_steps or ()
        _validate_step_replacement(task.steps, replacement_steps)
        data["steps"] = replacement_steps
    if patch.step_id is not None and patch.step_status is not None:
        updated_steps: list[TaskStep] = []
        found = False
        for step in task.steps:
            if step.id == patch.step_id:
                found = True
                if (
                    step.status is TaskStepStatus.DONE
                    and patch.step_status is not TaskStepStatus.DONE
                ):
                    raise ValueError("done task step cannot be rolled back")
                step_data = step.model_dump(mode="python")
                step_data["status"] = patch.step_status
                if "step_note" in patch.model_fields_set:
                    step_data["note"] = patch.step_note
                updated_steps.append(TaskStep.model_validate(step_data))
            else:
                updated_steps.append(step)
        if not found:
            raise KeyError(f"任务步骤不存在：{patch.step_id}")
        data["steps"] = tuple(updated_steps)
    if patch.run_id is not None:
        data["run_ids"] = _merge_entries(task.run_ids, (patch.run_id,))
    data["revision"] = task.revision + 1
    data["updated_at"] = now
    return Task.model_validate(data)


def _validate_step_replacement(
    existing: Sequence[TaskStep],
    replacement: Sequence[TaskStep],
) -> None:
    """重排计划不能删除或回退已经开始执行的步骤。"""

    replacement_by_id = {step.id: step for step in replacement}
    for step in existing:
        if step.status not in {
            TaskStepStatus.DONE,
            TaskStepStatus.IN_PROGRESS,
        }:
            continue
        candidate = replacement_by_id.get(step.id)
        if candidate is None:
            raise ValueError(
                f"replace_steps cannot delete protected step: {step.id}"
            )
        if step.status is TaskStepStatus.DONE:
            if candidate.status is not TaskStepStatus.DONE:
                raise ValueError(
                    f"replace_steps cannot roll back done step: {step.id}"
                )
        elif candidate.status is TaskStepStatus.TODO:
            raise ValueError(
                f"replace_steps cannot roll back in_progress step: {step.id}"
            )


def _merge_entries(
    existing: Sequence[str],
    new: Sequence[str],
) -> tuple[str, ...]:
    merged: list[str] = []
    seen: set[str] = set()
    for entry in (*existing, *new):
        text = " ".join(entry.split()).strip()
        if text and text not in seen:
            merged.append(text)
            seen.add(text)
    return tuple(merged)


def _validate_task_id(task_id: str) -> str:
    if not isinstance(task_id, str):
        raise TypeError("task_id must be a string")
    normalized = task_id.strip().lower()
    if not _TASK_ID_RE.fullmatch(normalized):
        raise ValueError("task_id must be a 32-character hexadecimal string")
    return normalized


def _validate_task_prefix(identifier: str) -> str:
    if not _TASK_PREFIX_RE.fullmatch(identifier):
        raise ValueError(
            "task identifier must be a 4-32 character hexadecimal prefix"
        )
    return identifier


def _normalize_required_entry(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    normalized = " ".join(value.split()).strip()
    if not normalized:
        raise ValueError(f"{field_name} cannot be empty")
    return normalized


__all__ = ["DEFAULT_TASKS_DIR", "MAX_TASK_FILE_BYTES", "FileTaskStore"]
