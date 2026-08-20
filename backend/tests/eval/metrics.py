"""指标聚合与报告输出。

每条场景产出：
- 通过 / 工具 / Task / 文件 / 回答 / 压缩 各维度检查结果；
- steps、工具调用次数、总 token、耗时。

汇总报告输出：
- 场景通过率、工具选择准确率、Task 状态正确率、安全组通过率；
- 平均 steps / tool calls / tokens / 耗时；
- 失败归因（每条失败场景列出失败断言项）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.agent.result import AgentResult

from .assertions import CheckResult


@dataclass
class ScenarioMetric:
    """单条场景的一次运行指标。"""

    scenario_id: str
    group: str
    name: str
    passed: bool
    checks: list[CheckResult] = field(default_factory=list)
    steps: int = 0
    tool_calls: int = 0
    tokens: int = 0
    duration_s: float = 0.0
    error: str | None = None
    run: int = 1
    stop_reason: str | None = None

    @property
    def failed_checks(self) -> list[CheckResult]:
        return [check for check in self.checks if not check.ok]


@dataclass
class EvalReport:
    """一次评测运行的汇总报告。"""

    metrics: list[ScenarioMetric] = field(default_factory=list)
    provider: str = "默认"
    model: str = "默认"
    run_root: str | None = None
    generated_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )

    @property
    def total(self) -> int:
        return len(self.metrics)

    @property
    def scenario_count(self) -> int:
        return len({metric.scenario_id for metric in self.metrics})

    @property
    def passed_count(self) -> int:
        return sum(1 for metric in self.metrics if metric.passed)

    @property
    def pass_rate(self) -> float:
        return self.passed_count / self.total if self.total else 0.0

    def _check_rate(self, name: str) -> float:
        relevant = [
            metric
            for metric in self.metrics
            if any(
                check.name == name and check.applicable
                for check in metric.checks
            )
        ]
        if not relevant:
            return 0.0
        passed = sum(
            1
            for m in relevant
            if any(
                c.name == name and c.applicable and c.ok
                for c in m.checks
            )
        )
        return passed / len(relevant)

    @property
    def tool_selection_rate(self) -> float:
        """工具选择准确率：tools 检查通过比例。"""

        return self._check_rate("tools")

    @property
    def task_state_rate(self) -> float:
        """Task 状态正确率：task 检查通过比例。"""

        return self._check_rate("task")

    @property
    def safety_pass_rate(self) -> float:
        """安全组通过率：group=safety 场景通过比例。"""

        safety = [m for m in self.metrics if m.group == "safety"]
        if not safety:
            return 0.0
        return sum(1 for m in safety if m.passed) / len(safety)

    def _avg(self, key: str) -> float:
        values = [
            getattr(m, key) for m in self.metrics if m.checks and not m.error
        ]
        return sum(values) / len(values) if values else 0.0

    @property
    def avg_steps(self) -> float:
        return self._avg("steps")

    @property
    def avg_tool_calls(self) -> float:
        return self._avg("tool_calls")

    @property
    def avg_tokens(self) -> float:
        return self._avg("tokens")

    @property
    def avg_duration_s(self) -> float:
        return self._avg("duration_s")


def metric_from_outcome(
    scenario: object,
    outcome: object,
    checks: list,
    passed: bool,
    *,
    run: int = 1,
) -> ScenarioMetric:
    """从一次运行结果构造单场景指标。"""

    result: AgentResult | None = getattr(outcome, "result", None)
    return ScenarioMetric(
        scenario_id=getattr(scenario, "id", "?"),
        group=getattr(scenario, "group", "?"),
        name=getattr(scenario, "name", "?"),
        passed=passed,
        checks=checks,
        steps=result.steps if result is not None else 0,
        tool_calls=len(result.tool_calls) if result is not None else 0,
        tokens=result.usage.total_tokens if result is not None else 0,
        duration_s=getattr(outcome, "duration_s", 0.0),
        error=getattr(outcome, "error", None),
        run=run,
        stop_reason=(result.stop_reason.value if result is not None else None),
    )


def render_report(report: EvalReport) -> str:
    """渲染 Markdown 格式的汇总报告。"""

    lines: list[str] = [
        "# Vesta Eval Report",
        "",
        "## 汇总",
        "",
        "| 指标 | 值 |",
        "| --- | --- |",
        f"| 唯一场景数 | {report.scenario_count} |",
        f"| 运行样本数 | {report.total} |",
        f"| 通过样本数 | {report.passed_count} |",
        f"| 样本通过率 | {report.pass_rate:.1%} |",
        f"| 工具选择准确率 | {report.tool_selection_rate:.1%} |",
        f"| Task 状态正确率 | {report.task_state_rate:.1%} |",
        f"| 安全组通过率 | {report.safety_pass_rate:.1%} |",
        f"| 平均 steps | {report.avg_steps:.1f} |",
        f"| 平均工具调用 | {report.avg_tool_calls:.1f} |",
        f"| 平均 tokens | {report.avg_tokens:.0f} |",
        f"| 平均耗时(s) | {report.avg_duration_s:.1f} |",
        "",
        "## 运行信息",
        "",
        f"- Provider：{report.provider}",
        f"- Model：{report.model}",
        f"- 生成时间：{report.generated_at}",
        f"- 运行现场：{report.run_root or '-'}",
        "",
        "## 分场景",
        "",
        "| ID | 分组 | 名称 | Run | 通过 | stop | steps | 工具 | "
        "tokens | 耗时(s) | 失败项 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for metric in report.metrics:
        failed_names = ",".join(c.name for c in metric.failed_checks) or "-"
        lines.append(
            "| {id} | {group} | {name} | {run} | {ok} | {stop} | "
            "{steps} | {tools} | {tokens} | "
            "{dur:.1f} | {failed} |".format(
                id=metric.scenario_id,
                group=metric.group,
                name=metric.name,
                run=metric.run,
                ok="✅" if metric.passed else "❌",
                stop=metric.stop_reason or "-",
                steps=metric.steps,
                tools=metric.tool_calls,
                tokens=metric.tokens,
                dur=metric.duration_s,
                failed=failed_names,
            )
        )
    lines.extend(
        [
            "",
            "## 失败归因",
            "",
        ]
    )
    failures = [m for m in report.metrics if not m.passed]
    if not failures:
        lines.append("无失败场景。")
    else:
        for metric in failures:
            lines.append(f"### {metric.scenario_id} · {metric.name}")
            for check in metric.failed_checks:
                lines.append(f"- [{check.name}] {check.detail}")
            if metric.error:
                lines.append(f"- 运行错误：{metric.error}")
            lines.append("")
    return "\n".join(lines)


__all__ = [
    "EvalReport",
    "ScenarioMetric",
    "metric_from_outcome",
    "render_report",
]
