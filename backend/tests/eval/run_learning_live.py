"""Skill Learning 真实模型 Live Eval runner（正式 Eval 工具）。

用法（在 backend 目录）：

    # 默认 provider（.env MODEL_DEFAULT_PROVIDER）+ model，跑 learning 组
    .venv/bin/python -m tests.eval.run_learning_live --runs 3 --print

    # 指定 provider / 报告路径
    .venv/bin/python -m tests.eval.run_learning_live --provider deepseek --runs 3 \\
        --out tests/eval/reports/skill_learning_live_20260818.md --print

与 run_live 不同：learning 组不走普通 Agent Run，而是用真实模型驱动
SkillLearningService（Pattern Mining + Procedure Distillation），并记录
真实模型的实际判断、Candidate 质量、Token 与 Latency。
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from app.models.config import ModelSettings
from app.models.registry import ModelAdapterRegistry

from .learning_harness import run_learning_scenario
from .learning_judge import ScenarioVerdict, aggregate, judge_scenario
from .loader import load_scenarios, select_scenarios

REPORTS_DIR = Path(__file__).resolve().parent / "reports"

# 每个场景执行 Human Gate 时的默认动作：Live 中 candidate 名不可预知，按全部处理。
_ACCEPT_ALL_SCENARIOS = frozenset({"learning-07"})
_REJECT_ALL_SCENARIOS = frozenset({"learning-08"})


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run real-model Skill Learning Live Eval scenarios."
    )
    parser.add_argument("--group", action="append", default=["learning"])
    parser.add_argument("--scenario", action="append")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--provider", help="真实模型 provider（默认 .env 默认值）。")
    parser.add_argument("--model", help="真实模型名（默认 provider 默认模型）。")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="报告输出路径（默认 tests/eval/reports/skill_learning_live_<日期>.md）。",
    )
    parser.add_argument(
        "--print",
        action="store_true",
        help="把报告打印到终端。",
    )
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    return args


def _task_input_preview(scenario) -> dict:
    """场景输入摘要（completed task 数量 / 标题 / batch_size）。"""

    titles = [task.title for task in scenario.initial_tasks]
    return {
        "completed_task_count": len(titles),
        "task_titles": titles[:10],
        "batch_size": scenario.expect.learning.batch_size,
    }


def _candidate_record(candidate) -> dict:
    return {
        "id": candidate.id,
        "action": candidate.action.value,
        "proposed_name": candidate.proposed_name,
        "existing_skill_name": candidate.existing_skill_name,
        "status": candidate.status.value,
        "reason": candidate.reason,
        "procedure": list(candidate.procedure),
        "pitfalls": list(candidate.pitfalls),
        "verification": list(candidate.verification),
        "source_task_ids": list(candidate.source_task_ids),
    }


def _run_record(scenario, run_index: int, outcome, verdict: ScenarioVerdict) -> dict:
    mining = outcome.mining
    clusters = [
        {
            "pattern_name": cluster.pattern_name,
            "task_ids": list(cluster.task_ids),
            "similarity_reason": cluster.similarity_reason,
            "reusable_value": cluster.reusable_value,
        }
        for cluster in (mining.clusters if mining is not None else ())
    ]
    return {
        "scenario_id": scenario.id,
        "scenario_name": scenario.name,
        "run": run_index,
        "input": _task_input_preview(scenario),
        "pattern_mining": {
            "cluster_count": len(clusters),
            "clusters": clusters,
        },
        "distillations": [
            {
                "cluster_name": d.cluster_name,
                "action": d.action,
                "reason": d.reason,
                "proposed_name": d.proposed_name,
                "existing_skill_name": d.existing_skill_name,
                "related_skill_names": list(d.related_skill_names),
                "error": d.error,
            }
            for d in (getattr(mining, "distillations", ()) or ())
        ],
        "candidates": [_candidate_record(c) for c in outcome.candidates],
        "created_skills": list(outcome.created_skills),
        "error": outcome.error,
        "usage": {
            "model_calls": (getattr(mining, "pattern_mining_calls", 0) or 0)
            + (getattr(mining, "distillation_calls", 0) or 0),
            "input_tokens": getattr(mining, "input_tokens", 0) or 0,
            "output_tokens": getattr(mining, "output_tokens", 0) or 0,
            "total_tokens": getattr(mining, "total_tokens", 0) or 0,
            "duration_ms": getattr(mining, "total_duration_ms", 0.0) or 0.0,
        },
        "verdict": {
            "passed": verdict.passed,
            "reasons": verdict.reasons,
            "pattern_detected": verdict.pattern_detected,
            "abstained": verdict.abstained,
            "negative_scenario": verdict.negative_scenario,
            "duplicate_scenario": verdict.duplicate_scenario,
            "precision": [
                c.precision for c in verdict.clusters if c.precision is not None
            ],
            "recall": [c.recall for c in verdict.clusters if c.recall is not None],
            "action_correct": verdict.action_correct,
            "false_positive": verdict.false_positive,
            "duplicate_candidate": verdict.duplicate_candidate,
            "pitfall_recall": verdict.pitfall_recall,
            "pitfall_found": list(verdict.pitfall_found),
        },
    }


async def main(args: argparse.Namespace) -> int:
    scenarios = select_scenarios(
        load_scenarios(),
        scenario_ids=tuple(args.scenario or ()),
        groups=tuple(args.group or ()),
    )
    if not scenarios:
        print("没有匹配的 learning 场景。", file=sys.stderr)
        return 2

    settings = ModelSettings()
    registry = ModelAdapterRegistry(settings)
    configured = settings.configured_providers()
    provider = args.provider
    if provider is None:
        provider = (
            settings.model_default_provider.value
            if settings.model_default_provider in configured
            else (configured[0] if configured else None)
        )
    if provider is None:
        print("没有可用的真实模型 provider。", file=sys.stderr)
        return 2
    model = args.model
    if model is None:
        try:
            model = registry.get(provider).default_model
        except Exception:
            model = None

    print(
        f"Skill Learning Live Eval · provider={provider} · model={model} · "
        f"scenarios={len(scenarios)} · runs={args.runs}"
    )

    runs: list[dict] = []
    verdicts: list[ScenarioVerdict] = []
    outcomes: list = []
    root = Path(tempfile.mkdtemp(prefix="oneagent-skill-learning-live-"))
    for scenario in scenarios:
        accept_all = scenario.id in _ACCEPT_ALL_SCENARIOS
        reject_all = scenario.id in _REJECT_ALL_SCENARIOS
        for run_index in range(1, args.runs + 1):
            print(
                f"  [{scenario.id}] run {run_index}/{args.runs} "
                f"({scenario.name})"
            )
            run_root = root / scenario.id / f"run-{run_index}"
            outcome = await run_learning_scenario(
                scenario,
                root=run_root,
                registry=registry,
                provider=provider,
                model=model,
                accept_all=accept_all,
                reject_all=reject_all,
            )
            verdict = judge_scenario(scenario, outcome)
            record = _run_record(scenario, run_index, outcome, verdict)
            runs.append(record)
            verdicts.append(verdict)
            outcomes.append(outcome)
            print(
                f"      verdict={'PASS' if verdict.passed else 'FAIL'} · "
                f"clusters={len(verdict.clusters)} · "
                f"candidates={len(outcome.candidates)}"
            )

    summary = aggregate(verdicts, outcomes)
    report_path = args.out or (
        REPORTS_DIR / f"skill_learning_live_{datetime.now(UTC):%Y%m%d}.md"
    )
    report = _render_report(
        provider=provider,
        model=model,
        scenarios=scenarios,
        runs=runs,
        summary=summary,
        generated_at=datetime.now(UTC).isoformat(),
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    print(f"\n报告已写入：{report_path}")
    if args.print:
        print(report)

    failures = sum(1 for verdict in verdicts if not verdict.passed)
    return 1 if failures else 0


def _render_report(
    *,
    provider: str,
    model: str,
    scenarios,
    runs: list[dict],
    summary,
    generated_at: str,
) -> str:
    lines: list[str] = []
    lines.append("# Skill Learning V1 Live Eval")
    lines.append("")
    lines.append("> **REAL MODEL** — 不是 Fake/Mock。")
    lines.append("")
    lines.append(f"- Date: {generated_at}")
    lines.append(f"- Provider: {provider}")
    lines.append(f"- Model: {model}")
    lines.append(f"- Config: batch_size={scenarios[0].expect.learning.batch_size}"
                 f" (min_cluster_size 默认 3)")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- scenarios: {len(scenarios)}")
    lines.append(f"- runs: {summary.total_runs}")
    lines.append(f"- pass rate: {summary.pass_rate:.0%} "
                 f"({summary.passed_runs}/{summary.total_runs})")
    lines.append(f"- cluster precision (avg): {_fmt_opt(summary.avg_precision)}")
    lines.append(f"- cluster recall (avg): {_fmt_opt(summary.avg_recall)}")
    lines.append(
        f"- pattern detection recall: {_fmt_opt(summary.pattern_detection_recall)} "
        f"({summary.pattern_detected_runs}/{summary.pattern_positive_runs})"
    )
    lines.append(
        f"- action accuracy (create/update/none): "
        f"{_fmt_opt(summary.action_accuracy)} "
        f"({summary.action_correct}/{summary.action_total})"
    )
    lines.append(
        f"- positive abstention rate: {_fmt_opt(summary.positive_abstention_rate)} "
        f"({summary.abstained_runs}/{summary.abstention_denominator})"
    )
    lines.append(
        f"- false positive rate: {_fmt_opt(summary.false_positive_rate)} "
        f"({summary.false_positive_runs}/{summary.negative_runs})"
    )
    lines.append(
        f"- duplicate candidate rate: "
        f"{_fmt_opt(summary.duplicate_candidate_rate)} "
        f"({summary.duplicate_runs}/{summary.duplicate_scenario_runs})"
    )
    lines.append(f"- pitfall recall (avg): {_fmt_opt(summary.avg_pitfall_recall)}")
    lines.append(f"- total model calls: {summary.total_model_calls}")
    lines.append(f"- total input tokens: {summary.total_input_tokens}")
    lines.append(f"- total output tokens: {summary.total_output_tokens}")
    lines.append(f"- total tokens: {summary.total_tokens}")
    lines.append(f"- total duration: {summary.total_duration_ms / 1000:.1f}s")
    lines.append(
        f"- avg tokens / eval batch: {summary.avg_tokens_per_batch:.0f}"
    )
    lines.append(
        f"- avg tokens / scanned task: "
        f"{_fmt_opt(summary.avg_tokens_per_scanned_task)}"
    )
    lines.append(
        f"- avg latency / eval batch: {summary.avg_duration_ms / 1000:.1f}s"
    )
    # 真正 20-Task 场景单独统计成本。
    twenty_runs = [r for r in runs if r["input"]["completed_task_count"] == 20]
    if twenty_runs:
        twenty_tokens = sum(r["usage"]["total_tokens"] for r in twenty_runs)
        twenty_duration = sum(r["usage"]["duration_ms"] for r in twenty_runs)
        lines.append(
            f"- 20-Task 场景单独统计: runs={len(twenty_runs)} · "
            f"tokens={twenty_tokens} · "
            f"avg tokens/batch={twenty_tokens / len(twenty_runs):.0f} · "
            f"duration={twenty_duration / 1000:.1f}s · "
            f"avg latency/batch={twenty_duration / len(twenty_runs) / 1000:.1f}s"
        )
    lines.append("")
    lines.append("## Scenario Results")
    lines.append("")
    for record in runs:
        lines.extend(_render_run(record))
    return "\n".join(lines) + "\n"


def _render_run(record: dict) -> list[str]:
    lines: list[str] = []
    scenario_id = record["scenario_id"]
    verdict = record["verdict"]
    lines.append(f"### {scenario_id} · run {record['run']}")
    lines.append("")
    lines.append(f"**{record['scenario_name']}**")
    lines.append("")
    inp = record["input"]
    lines.append(f"Input: completed task count={inp['completed_task_count']}, "
                 f"batch_size={inp['batch_size']}")
    lines.append("")
    lines.append("Task titles:")
    for title in inp["task_titles"]:
        lines.append(f"- {title}")
    lines.append("")
    lines.append("Actual Pattern Mining:")
    pm = record["pattern_mining"]
    if not pm["clusters"]:
        lines.append("```json\n{\"clusters\": []}\n```")
    else:
        lines.append("```json")
        lines.append(json.dumps(pm["clusters"], ensure_ascii=False, indent=2))
        lines.append("```")
    lines.append("")
    lines.append("Actual Distillation:")
    if not record["distillations"]:
        lines.append("（无蒸馏调用）")
    else:
        for dist in record["distillations"]:
            lines.append("```json")
            lines.append(
                json.dumps(
                    {
                        "cluster": dist["cluster_name"],
                        "action": dist["action"],
                        "reason": dist["reason"],
                        "proposed_name": dist["proposed_name"],
                        "existing_skill_name": dist["existing_skill_name"],
                        "related_skills": dist["related_skill_names"],
                        "error": dist["error"],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            lines.append("```")
    lines.append("")
    lines.append("Actual Candidates:")
    if not record["candidates"]:
        lines.append("（无候选）")
    else:
        for candidate in record["candidates"]:
            lines.append("```json")
            lines.append(json.dumps(candidate, ensure_ascii=False, indent=2))
            lines.append("```")
    lines.append("")
    lines.append(f"Created Skills: {record['created_skills'] or '（无）'}")
    lines.append(f"Error: {record['error'] or '（无）'}")
    lines.append("")
    usage = record["usage"]
    lines.append(
        "Usage: "
        f"calls={usage['model_calls']}, "
        f"in={usage['input_tokens']}, out={usage['output_tokens']}, "
        f"total={usage['total_tokens']}, "
        f"latency={usage['duration_ms'] / 1000:.1f}s"
    )
    lines.append("")
    v = verdict
    lines.append(f"Verdict: **{'PASS' if v['passed'] else 'FAIL'}**")
    if v["reasons"]:
        for reason in v["reasons"]:
            lines.append(f"- {reason}")
    lines.append(
        f"- pattern_detected={v['pattern_detected']} "
        f"abstained={v['abstained']} "
        f"precision={_fmt_opt_list(v['precision'])} "
        f"recall={_fmt_opt_list(v['recall'])} "
        f"action_correct={v['action_correct']} "
        f"false_positive={v['false_positive']} "
        f"duplicate={v['duplicate_candidate']} "
        f"pitfall_recall={_fmt_opt(v['pitfall_recall'])}"
    )
    lines.append("")
    lines.append("---")
    lines.append("")
    return lines


def _fmt_opt(value) -> str:
    return f"{value:.2f}" if value is not None else "N/A"


def _fmt_opt_list(values) -> str:
    return ", ".join(f"{v:.2f}" for v in values) if values else "N/A"


if __name__ == "__main__":
    import asyncio

    sys.exit(asyncio.run(main(_parse_args())))
