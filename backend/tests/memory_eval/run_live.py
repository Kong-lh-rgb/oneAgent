"""使用真实模型运行多阶段长期记忆测评。"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from app.models.config import ModelSettings

from .assertions import check_phase
from .harness import run_scenario
from .loader import load_scenarios, select_scenarios
from .metrics import MemoryEvalReport, metric_from_phase, render_report

_REPORTS_DIR = Path(__file__).resolve().parent / "reports"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run live long-term memory evals.")
    parser.add_argument("--scenario", action="append", help="指定场景 ID。")
    parser.add_argument("--tag", action="append", help="按标签筛选。")
    parser.add_argument("--runs", type=int, default=1, help="每条场景运行次数。")
    parser.add_argument("--provider", help="模型提供商。")
    parser.add_argument("--model", help="模型名。")
    parser.add_argument("--root", type=Path, help="保留运行现场的根目录。")
    parser.add_argument("--out", type=Path, help="报告输出路径。")
    parser.add_argument(
        "--compare-off",
        action="store_true",
        help="同时运行 Memory OFF 对照。",
    )
    parser.add_argument("--print", action="store_true", help="打印完整报告。")
    parser.add_argument("--allow-failures", action="store_true")
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    return args


async def main(args: argparse.Namespace) -> int:
    scenarios = select_scenarios(
        load_scenarios(),
        scenario_ids=tuple(args.scenario or ()),
        tags=tuple(args.tag or ()),
    )
    if not scenarios:
        print("没有匹配的 Memory 场景。", file=sys.stderr)
        return 2
    settings = ModelSettings()
    provider = args.provider or settings.model_default_provider.value
    try:
        model = args.model or settings.provider_config(provider).model
    except ValueError:
        model = args.model or "未知"
    invocation = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
    root = (
        args.root / f"memory-eval-{invocation}"
        if args.root
        else Path(tempfile.mkdtemp(prefix="vesta-memory-eval-"))
    )
    report = MemoryEvalReport(provider=provider, model=model, root=str(root))
    modes = (("on", True), ("off", False)) if args.compare_off else (("on", True),)
    for scenario in scenarios:
        for run_index in range(1, args.runs + 1):
            for mode, enabled in modes:
                outcome = await run_scenario(
                    scenario,
                    root=root / scenario.id / f"run-{run_index}" / mode,
                    provider=args.provider,
                    model=args.model,
                    memory_enabled=enabled,
                )
                for phase in outcome.phases:
                    checks, passed = check_phase(
                        scenario,
                        phase,
                        aliases=outcome.aliases,
                        mode=mode,
                    )
                    metric = metric_from_phase(
                        scenario.id,
                        scenario.name,
                        phase,
                        checks,
                        passed,
                        run=run_index,
                        mode=mode,
                    )
                    report.metrics.append(metric)
                    print(
                        f"{'✅' if passed else '❌'} {scenario.id}/{phase.phase.id} "
                        f"run#{run_index} mode={mode} reads={metric.memory_reads}"
                    )
    text = render_report(report)
    output = args.out or (
        _REPORTS_DIR
        / f"memory_report_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.md"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
    print(f"报告已保存：{output}")
    if args.print:
        print("\n" + text)
    if report.passed != report.total and not args.allow_failures:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(_parse_args())))
