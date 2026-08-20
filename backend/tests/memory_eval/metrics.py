"""长期记忆测评指标与 Markdown 报告。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.agent.events import AgentEventType

from .assertions import MemoryCheckResult
from .harness import MemoryEvalPhaseOutcome


@dataclass
class MemoryPhaseMetric:
    """一个 Memory Eval 阶段的指标。"""

    scenario_id: str
    scenario_name: str
    phase_id: str
    conversation: str
    passed: bool
    checks: list[MemoryCheckResult] = field(default_factory=list)
    steps: int = 0
    memory_reads: int = 0
    main_tokens: int = 0
    reflection_tokens: int = 0
    maintenance_tokens: int = 0
    duration_s: float = 0.0
    error: str | None = None
    run: int = 1
    mode: str = "on"

    @property
    def failed_checks(self) -> list[MemoryCheckResult]:
        return [check for check in self.checks if not check.ok]


@dataclass
class MemoryEvalReport:
    """多阶段长期记忆测评汇总。"""

    metrics: list[MemoryPhaseMetric] = field(default_factory=list)
    provider: str = "默认"
    model: str = "默认"
    root: str | None = None
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def total(self) -> int:
        return len(self.metrics)

    @property
    def passed(self) -> int:
        return sum(metric.passed for metric in self.metrics)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    def check_rate(self, name: str) -> float:
        relevant = [
            metric
            for metric in self.metrics
            if any(c.name == name for c in metric.checks)
        ]
        if not relevant:
            return 0.0
        passed = sum(
            any(check.name == name and check.ok for check in metric.checks)
            for metric in relevant
        )
        return passed / len(relevant)


def metric_from_phase(
    scenario_id: str,
    scenario_name: str,
    outcome: MemoryEvalPhaseOutcome,
    checks: list[MemoryCheckResult],
    passed: bool,
    *,
    run: int,
    mode: str,
) -> MemoryPhaseMetric:
    result = outcome.result
    reflection_tokens = sum(
        event.usage.total_tokens
        for event in outcome.events
        if event.type
        in {
            AgentEventType.MEMORY_REFLECTION_COMPLETED,
            AgentEventType.MEMORY_REFLECTION_FAILED,
        }
        and event.usage is not None
    )
    maintenance_tokens = sum(
        event.usage.total_tokens
        for event in outcome.events
        if event.type
        in {
            AgentEventType.MEMORY_MAINTENANCE_COMPLETED,
            AgentEventType.MEMORY_MAINTENANCE_FAILED,
        }
        and event.usage is not None
    )
    reads = 0
    if result is not None:
        reads = sum(
            record.tool_call.name == "memory_read" for record in result.tool_calls
        )
    return MemoryPhaseMetric(
        scenario_id=scenario_id,
        scenario_name=scenario_name,
        phase_id=outcome.phase.id,
        conversation=outcome.phase.conversation,
        passed=passed,
        checks=checks,
        steps=result.steps if result is not None else 0,
        memory_reads=reads,
        main_tokens=result.usage.total_tokens if result is not None else 0,
        reflection_tokens=reflection_tokens,
        maintenance_tokens=maintenance_tokens,
        duration_s=outcome.duration_s,
        error=outcome.error,
        run=run,
        mode=mode,
    )


def render_report(report: MemoryEvalReport) -> str:
    lines = [
        "# Vesta Memory Eval Report",
        "",
        "## 汇总",
        "",
        "| 指标 | 值 |",
        "| --- | --- |",
        f"| 阶段样本数 | {report.total} |",
        f"| 通过数 | {report.passed} |",
        f"| 阶段通过率 | {report.pass_rate:.1%} |",
        f"| Reflection Action 准确率 | {report.check_rate('reflection_action'):.1%} |",
        f"| Recall 准确率 | {report.check_rate('recall'):.1%} |",
        f"| 回答关键事实通过率 | {report.check_rate('answer'):.1%} |",
        f"| Store 状态通过率 | {report.check_rate('stored_memory'):.1%} |",
        "",
        f"- Provider：{report.provider}",
        f"- Model：{report.model}",
        f"- 生成时间：{report.generated_at}",
        f"- 运行现场：{report.root or '-'}",
        "",
        "## 分阶段",
        "",
        "| 场景 | Phase | 会话 | Run | 模式 | 通过 | steps | reads | Main tokens | "
        "Reflection tokens | Maintenance tokens | 耗时(s) | 失败项 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | "
        "--- | --- | --- |",
    ]
    for metric in report.metrics:
        failures = ",".join(check.name for check in metric.failed_checks) or "-"
        lines.append(
            f"| {metric.scenario_id} | {metric.phase_id} | "
            f"{metric.conversation} | {metric.run} | {metric.mode} | "
            f"{'✅' if metric.passed else '❌'} | {metric.steps} | "
            f"{metric.memory_reads} | {metric.main_tokens} | "
            f"{metric.reflection_tokens} | {metric.maintenance_tokens} | "
            f"{metric.duration_s:.1f} | {failures} |"
        )
    lines.extend(["", "## 失败归因", ""])
    failed = [metric for metric in report.metrics if not metric.passed]
    if not failed:
        lines.append("无失败阶段。")
    for metric in failed:
        lines.append(f"### {metric.scenario_id}/{metric.phase_id} · {metric.mode}")
        for check in metric.failed_checks:
            lines.append(f"- [{check.name}] {check.detail}")
        if metric.error:
            lines.append(f"- 运行错误：{metric.error}")
        lines.append("")
    return "\n".join(lines)


__all__ = [
    "MemoryEvalReport",
    "MemoryPhaseMetric",
    "metric_from_phase",
    "render_report",
]
