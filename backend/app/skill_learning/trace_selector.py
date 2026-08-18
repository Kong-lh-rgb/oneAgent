"""从 Run Trace 中筛选"真正属于某个 Task"的执行事件。

数据流（替代旧的 Task → Run → 整个 Run Events）：

    Task
    → Task.run_ids                      粗粒度索引（去哪找）
    → Run Trace                         加载这些 Run 的事件
    → task_update anchors               成功更新当前 Task 的 task_update 锚点
    → relevant Agent Step ranges        锚点之间的 Agent Step 区间
    → Events                            只保留区间内的事件
    → TraceEvidenceBuilder              （Event → Evidence 文本，本模块不负责）

职责边界：
- 本模块只回答"哪些 Event 属于这个 Task"；
- TraceEvidenceBuilder 继续负责 "Event → Evidence 文本"。

关键规则：
- 只有 TOOL_COMPLETED + tool_call.name == "task_update" + tool_result.success
  + arguments.task_id 指向当前 Task（完整 ID 或合法前缀）才算有效 Anchor；
  失败 / 更新其他 Task 的 task_update 不能作为 Anchor。
- 优先用 TaskStep 生命周期（step_id + in_progress/done/blocked）切精确区间；
- 没有 in_progress 锚点时用 bounded backward window（模块常量，默认最近 5 个
  Agent Step），不无限向前扫描；
- 无 step_id 的普通 task_update（goal/state/constraints/facts/status）用
  anchor 附近的 bounded window，不丢弃整个 Run；
- 同一 Event 被多个区间覆盖时只保留一次，按 run + sequence 保持原始执行顺序；
- 最终事件数严格 <= max_events（硬上限）。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from app.agent.events import AgentEvent, AgentEventType
from app.task import Task

# 无 in_progress 锚点 / 普通 task_update 时的 backward window（Agent Step 数）。
# 配置化或至少模块常量，不要无限向前扫描整个 Run。
_DEFAULT_BACKWARD_WINDOW_STEPS = 5

_TASK_UPDATE_TOOL = "task_update"
_STEP_LIFECYCLE_STATUSES = frozenset({"in_progress", "done", "blocked"})


@dataclass(frozen=True)
class _Anchor:
    """一个有效的 Task Anchor（成功更新当前 Task 的 task_update TOOL_COMPLETED）。"""

    run_id: str
    step: int
    step_id: str | None
    step_status: str | None


class TaskTraceSelector:
    """从多个 Run 的完整 Trace 中选出当前 Task 的相关事件。"""

    def __init__(
        self,
        *,
        backward_window_steps: int = _DEFAULT_BACKWARD_WINDOW_STEPS,
    ) -> None:
        self.backward_window_steps = max(1, backward_window_steps)

    def select(
        self,
        task: Task,
        run_events: dict[str, tuple[AgentEvent, ...]],
        *,
        max_events: int | None = None,
    ) -> tuple[AgentEvent, ...]:
        """返回当前 Task 相关的 AgentEvent（去重、保序、严格 <= max_events）。

        ``run_events`` 必须已按 ``sequence`` 升序（Trace store 的 load_events 保证）。
        无有效 Anchor / 无法构造区间时返回空 tuple（调用方走 Task-only fallback）。
        """

        anchors = self._find_task_update_anchors(task, run_events)
        if not anchors:
            return ()
        coverage = self._build_relevant_step_ranges(anchors, run_events)
        if not coverage:
            return ()
        return self._select_events_for_ranges(
            task, coverage, run_events, max_events=max_events
        )

    # ------------------------------------------------------------------
    # 一、Task Anchor 识别
    # ------------------------------------------------------------------

    def _find_task_update_anchors(
        self,
        task: Task,
        run_events: dict[str, tuple[AgentEvent, ...]],
    ) -> tuple[_Anchor, ...]:
        """只把"成功更新当前 Task"的 task_update TOOL_COMPLETED 当作 Anchor。

        task_id 兼容唯一前缀：参数是当前完整 Task ID 的合法前缀即匹配。
        """

        anchors: list[_Anchor] = []
        for run_id, events in run_events.items():
            for event in events:
                if event.type is not AgentEventType.TOOL_COMPLETED:
                    continue
                call = event.tool_call
                result = event.tool_result
                if call is None or result is None:
                    continue
                if call.name != _TASK_UPDATE_TOOL:
                    continue
                if not result.success:
                    # 失败的 task_update 不能作为 Anchor。
                    continue
                if event.step is None:
                    continue
                arguments = call.arguments
                if not isinstance(arguments, dict):
                    continue
                task_id_arg = arguments.get("task_id")
                if not isinstance(task_id_arg, str) or not task_id_arg.strip():
                    continue
                if not task.id.startswith(task_id_arg.strip()):
                    # 更新其他 Task 的 task_update 不算当前 Task 的 Anchor。
                    continue
                step_id = arguments.get("step_id")
                step_status = arguments.get("step_status")
                anchors.append(
                    _Anchor(
                        run_id=run_id,
                        step=event.step,
                        step_id=(
                            step_id.strip()
                            if isinstance(step_id, str) and step_id.strip()
                            else None
                        ),
                        step_status=(
                            step_status.strip()
                            if isinstance(step_status, str) and step_status.strip()
                            else None
                        ),
                    )
                )
        return tuple(anchors)

    # ------------------------------------------------------------------
    # 二、构建相关 Step 区间
    # ------------------------------------------------------------------

    def _build_relevant_step_ranges(
        self,
        anchors: tuple[_Anchor, ...],
        run_events: dict[str, tuple[AgentEvent, ...]],
    ) -> dict[str, set[int]]:
        """把 Anchors 折叠成"每个 Run 要保留的 Agent Step 编号集合"。

        - TaskStep 生命周期锚点（step_id + in_progress/done/blocked）优先，
          支持跨 Run 合并为一个执行区间；
        - 没有 in_progress 锚点时用 backward window；
        - 无 step_id 的普通 task_update 用 anchor 附近 bounded window。
        """

        run_order = tuple(run_events)
        run_max_step: dict[str, int] = {}
        for run_id, events in run_events.items():
            steps = [event.step for event in events if event.step is not None]
            run_max_step[run_id] = max(steps) if steps else 0

        coverage: dict[str, set[int]] = defaultdict(set)
        step_anchors: dict[str, list[tuple[int, str, int, str]]] = defaultdict(list)
        non_step_anchors: list[_Anchor] = []
        run_indices = {run_id: index for index, run_id in enumerate(run_order)}

        for anchor in anchors:
            if (
                anchor.step_id
                and anchor.step_status in _STEP_LIFECYCLE_STATUSES
            ):
                step_anchors[anchor.step_id].append(
                    (
                        run_indices[anchor.run_id],
                        anchor.run_id,
                        anchor.step,
                        anchor.step_status,
                    )
                )
            else:
                # 普通 task_update（goal/state/constraints/facts/status…）
                non_step_anchors.append(anchor)

        # 每个 TaskStep 的完整生命周期（含跨 Run）。
        for items in step_anchors.values():
            items.sort(key=lambda item: (item[0], item[2]))
            # (run_index, run_id, start_step)：有 in_progress 未闭合的区间
            open_segment: tuple[int, str, int] | None = None
            for run_index, run_id, step, status in items:
                if status == "in_progress":
                    if open_segment is None:
                        open_segment = (run_index, run_id, step)
                    # 已有未闭合的 in_progress：视为 continuation，保留最早的
                    # start anchor，不重置，直到 done / blocked 才闭合。
                else:  # done / blocked
                    if open_segment is None:
                        # 没有 in_progress 锚点 → bounded backward window。
                        self._add_backward_window(coverage, run_id, step)
                    else:
                        open_index, open_run_id, open_step = open_segment
                        if open_index == run_index:
                            # 同一 Run 内闭合。
                            coverage[run_id].update(range(open_step, step + 1))
                        else:
                            # 跨 Run：start Run 到结束 + 中间 Run 全部 + end 锚点
                            for index in range(open_index, run_index + 1):
                                mid_run_id = run_order[index]
                                if index == open_index:
                                    coverage[mid_run_id].update(
                                        range(
                                            open_step,
                                            run_max_step[mid_run_id] + 1,
                                        )
                                    )
                                elif index == run_index:
                                    coverage[mid_run_id].update(
                                        range(1, step + 1)
                                    )
                                else:
                                    coverage[mid_run_id].update(
                                        range(1, run_max_step[mid_run_id] + 1)
                                    )
                        open_segment = None
            if open_segment is not None:
                # 未闭合的 in_progress → 从锚点覆盖到 Run 结束。
                _, run_id, step = open_segment
                coverage[run_id].update(range(step, run_max_step[run_id] + 1))

        # 普通 task_update：anchor 附近 bounded window（前 N 步 + 当前锚点步）。
        for anchor in non_step_anchors:
            self._add_backward_window(coverage, anchor.run_id, anchor.step)

        return dict(coverage)

    def _add_backward_window(
        self,
        coverage: dict[str, set[int]],
        run_id: str,
        anchor_step: int,
    ) -> None:
        """从锚点向前取有限数量的 Agent Step（含锚点本身）。"""

        start = max(1, anchor_step - self.backward_window_steps + 1)
        coverage[run_id].update(range(start, anchor_step + 1))

    # ------------------------------------------------------------------
    # 三、按区间选 Events（去重 / 保序 / 硬上限）
    # ------------------------------------------------------------------

    def _select_events_for_ranges(
        self,
        task: Task,
        coverage: dict[str, set[int]],
        run_events: dict[str, tuple[AgentEvent, ...]],
        *,
        max_events: int | None,
    ) -> tuple[AgentEvent, ...]:
        """按 Run 顺序 + Run 内 sequence 顺序挑选事件，严格 <= max_events。

        同一 Event 被多个区间覆盖时因 step 集合天然去重（set），不会重复。
        """

        selected: list[AgentEvent] = []
        for run_id in task.run_ids:
            steps = coverage.get(run_id)
            if not steps:
                continue
            for event in run_events[run_id]:
                if event.step is not None and event.step in steps:
                    selected.append(event)
                    if max_events is not None and len(selected) >= max_events:
                        return tuple(selected)
        return tuple(selected)


__all__ = ["TaskTraceSelector"]
