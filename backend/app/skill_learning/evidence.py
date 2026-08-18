"""Trace Evidence Builder：把 AgentEvent 压缩成过程学习材料。

只提取与"可复用流程"有关的信息，绝不把全部 AgentEvent 原样交给模型：
- 工具调用序列（成功/失败、重试）；
- task_create / task_update 的变更（goal、steps、constraints、key_facts）；
- 失败工具调用与错误；
- 完成证据。

原始证据继续由 Task / Trace 作为事实源；这里只产出轻量摘要文本。
"""

from __future__ import annotations

import logging
from typing import Any

from app.agent.events import AgentEvent, AgentEventType
from app.task import Task

from .config import SkillLearningSettings

logger = logging.getLogger("oneagent.skill_learning.evidence")

_TASK_TOOL_NAMES = frozenset({"task_create", "task_update"})
_OBSERVABLE_TOOL_START = AgentEventType.TOOL_STARTED
_OBSERVABLE_TOOL_COMPLETE = AgentEventType.TOOL_COMPLETED
_OBSERVABLE_TYPES = frozenset(
    {
        AgentEventType.TOOL_STARTED,
        AgentEventType.TOOL_COMPLETED,
        AgentEventType.MODEL_COMPLETED,
        AgentEventType.AGENT_COMPLETED,
        AgentEventType.AGENT_FAILED,
    }
)


class TraceEvidenceBuilder:
    """从 Task + AgentEvent 序列构建确定性的过程摘要。"""

    def __init__(self, settings: SkillLearningSettings) -> None:
        self.settings = settings

    def build(
        self,
        task: Task,
        events: tuple[AgentEvent, ...] | list[AgentEvent],
    ) -> str:
        """压缩事件为过程学习文本；Trace 缺失时优雅降级到任务事实。"""

        if not events:
            return self._task_only(task, "没有可用的 Trace 事件")
        lines: list[str] = []
        tool_sequence: list[str] = []
        failed_calls: list[str] = []
        task_updates: list[str] = []
        completion: list[str] = []

        for event in events:
            if event.type not in _OBSERVABLE_TYPES:
                continue
            if event.type is _OBSERVABLE_TOOL_START:
                name = event.tool_call.name if event.tool_call else "?"
                tool_sequence.append(name)
            elif event.type is _OBSERVABLE_TOOL_COMPLETE:
                result = event.tool_result
                tool_name = event.tool_call.name if event.tool_call else "?"
                if result is not None and not result.success:
                    failed_calls.append(
                        f"{tool_name}: {result.error or 'failed'}"
                    )
                if tool_name in _TASK_TOOL_NAMES and result is not None:
                    summary = self._task_tool_summary(event)
                    if summary:
                        task_updates.append(summary)
            elif event.type is AgentEventType.AGENT_COMPLETED:
                completion.append("agent completed")
            elif event.type is AgentEventType.AGENT_FAILED:
                error = event.error or "agent failed"
                completion.append(f"agent failed: {error}")

        if tool_sequence:
            sequence = " → ".join(_dedupe_consecutive(tool_sequence))
            lines.append(f"工具调用序列: {sequence}")
        if failed_calls:
            lines.append("失败工具调用:")
            lines.extend(f"- {item}" for item in failed_calls[:20])
        if task_updates:
            lines.append("Task 变更:")
            lines.extend(f"- {item}" for item in task_updates[:30])
        if completion:
            lines.append("完成证据: " + "; ".join(completion))
        if not lines:
            lines.append("无工具调用（可能为纯问答 Run）")

        header = (
            f"Task: {task.title}\n"
            f"Goal: {task.goal or '（无）'}\n"
            "状态: "
            f"{task.status.value} · 步骤 {len(task.steps)} · run 数 {len(task.run_ids)}"
        )
        text = header + "\n" + "\n".join(lines)
        return _truncate(text, self.settings.skill_learning_max_evidence_chars)

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    def _task_tool_summary(self, event: AgentEvent) -> str | None:
        """从 task_create / task_update 事件提取变更摘要。"""

        call = event.tool_call
        result = event.tool_result
        if call is None or result is None:
            return None
        arguments = call.arguments or {}
        if not isinstance(arguments, dict):
            return None
        if call.name == "task_create":
            title = arguments.get("title")
            goal = arguments.get("goal")
            steps = arguments.get("steps")
            return (
                f"task_create title={title!r} goal={goal!r} "
                f"steps={_step_count(steps)}"
            )
        # task_update
        changed: list[str] = []
        for key in ("goal", "status", "add_constraints", "add_key_facts"):
            if arguments.get(key) is not None:
                changed.append(key)
        if arguments.get("replace_steps") is not None:
            changed.append("replace_steps")
        if arguments.get("step_id") is not None:
            changed.append(
                f"step:{arguments.get('step_id')}->{arguments.get('step_status')}"
            )
        if not changed:
            return None
        return "task_update " + ",".join(changed)

    def _task_only(self, task: Task, reason: str) -> str:
        final_steps = _task_steps(task)
        parts = [
            f"Task: {task.title}",
            f"Goal: {task.goal or '（无）'}",
            f"约束: {', '.join(task.constraints) or '（无）'}",
            f"关键事实: {', '.join(task.key_facts) or '（无）'}",
            f"最终步骤: {', '.join(final_steps) or '（无步骤）'}",
            f"run 数: {len(task.run_ids)}",
            f"备注: {reason}",
        ]
        return _truncate(
            "\n".join(parts),
            self.settings.skill_learning_max_evidence_chars,
        )


def _task_steps(task: Task) -> list[str]:
    return [
        step.title for step in task.steps if step.status.value == "done"
    ]


def _step_count(value: Any) -> int:
    if isinstance(value, (list, tuple)):
        return len(value)
    return 0


def _dedupe_consecutive(sequence: list[str]) -> list[str]:
    result: list[str] = []
    for item in sequence:
        if not result or result[-1] != item:
            result.append(item)
    return result


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "…[截断]"


__all__ = ["TraceEvidenceBuilder"]
