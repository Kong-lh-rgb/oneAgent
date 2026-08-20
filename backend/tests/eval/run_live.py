"""Live Eval：用真实模型运行场景并输出测评报告。

用法（在 backend 目录）：

    .venv/bin/python -m tests.eval.run_live
    .venv/bin/python -m tests.eval.run_live --group task
    .venv/bin/python -m tests.eval.run_live --scenario eval-01 eval-02 --runs 3
    .venv/bin/python -m tests.eval.run_live --provider deepseek \
    --model deepseek-v4-flash
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from app.models.config import ModelSettings
from tests.eval import harness
from tests.eval.assertions import run_checks
from tests.eval.loader import load_scenarios, select_scenarios
from tests.eval.metrics import EvalReport, metric_from_outcome, render_report

_DEFAULT_REPORTS_DIR = Path(__file__).resolve().parent / "reports"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run live eval scenarios.")
    parser.add_argument(
        "--group",
        action="append",
        help="只跑指定分组（可多次）。",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        help="只跑指定场景 ID（可多次）。",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="每条场景运行次数（默认 1，波动大的场景建议 3）。",
    )
    parser.add_argument("--provider", help="模型提供商（默认使用 .env 默认）。")
    parser.add_argument("--model", help="模型名。")
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="运行临时目录（默认系统临时目录；用于保留现场复现）。",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="报告输出路径（默认 tests/eval/reports/report_<时间戳>.md）。",
    )
    parser.add_argument(
        "--print",
        action="store_true",
        help="同时把报告打印到终端。",
    )
    parser.add_argument(
        "--allow-failures",
        action="store_true",
        help="存在失败场景时仍返回退出码 0（用于探索性运行）。",
    )
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    return args


async def _run_one(
    scenario,
    *,
    root: Path,
    run_index: int,
    provider: str | None,
    model: str | None,
) -> tuple:
    run_root = root / scenario.id / f"run-{run_index}"
    outcome = await harness.run_scenario(
        scenario,
        root=run_root,
        provider=provider,
        model=model,
    )
    checks, passed = await run_checks(scenario, outcome=outcome)
    metric = metric_from_outcome(
        scenario,
        outcome,
        checks,
        passed,
        run=run_index,
    )
    return metric, checks, passed


async def main(args: argparse.Namespace) -> int:
    all_scenarios = load_scenarios()
    scenarios = select_scenarios(
        all_scenarios,
        scenario_ids=tuple(args.scenario or ()),
        groups=tuple(args.group or ()),
    )
    # learning 组由 tests/eval/learning_harness 以 Mock 模型驱动，不走普通 Agent Run。
    explicit_learning = (
        "learning" in (args.group or ())
        or any(scenario.group == "learning" for scenario in scenarios)
    )
    if not (args.group or args.scenario):
        scenarios = tuple(
            scenario for scenario in scenarios if scenario.group != "learning"
        )
    elif explicit_learning:
        print(
            "learning 组场景由 tests/eval/learning_harness 驱动（Mock 模型），"
            "请运行 tests/test_skill_learning_eval.py 验证。",
            file=sys.stderr,
        )
        return 0
    if not scenarios:
        print("没有匹配的场景。", file=sys.stderr)
        return 2

    invocation_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
    root = (
        args.root / f"eval-{invocation_id}"
        if args.root is not None
        else Path(tempfile.mkdtemp(prefix="vesta-eval-"))
    )
    root.mkdir(parents=True, exist_ok=True)
    settings = ModelSettings()
    resolved_provider = (
        args.provider or settings.model_default_provider.value
    )
    try:
        resolved_model = (
            args.model or settings.provider_config(resolved_provider).model
        )
    except ValueError:
        resolved_model = args.model or "未知"
    report = EvalReport(
        provider=resolved_provider,
        model=resolved_model,
        run_root=str(root),
    )

    print(
        f"开始 Live Eval：{len(scenarios)} 条场景 × {args.runs} 次 "
        f"(provider={resolved_provider} model={resolved_model})"
    )
    for scenario in scenarios:
        for run_index in range(1, args.runs + 1):
            metric, _, passed = await _run_one(
                scenario,
                root=root,
                run_index=run_index,
                provider=args.provider,
                model=args.model,
            )
            report.metrics.append(metric)
            marker = "✅" if passed else "❌"
            print(
                f"  {marker} {scenario.id} run#{run_index} "
                f"steps={metric.steps} tools={metric.tool_calls} "
                f"tokens={metric.tokens} {metric.duration_s:.1f}s"
            )

    text = render_report(report)
    out_path = args.out or (
        _DEFAULT_REPORTS_DIR
        / f"report_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.md"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    print(f"\n报告已保存：{out_path}")
    if args.print:
        print("\n" + text)
    if report.passed_count != report.total and not args.allow_failures:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(_parse_args())))
