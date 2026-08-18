"""learning-10 真实 20-Task Eval 的增强测试。

覆盖：
- InitialTraceEvent.step 进入 AgentEvent；
- $task:<alias> 递归解析（成功 / unknown alias 明确失败）；
- expected_trace_steps exact judge；
- evidence_contains / evidence_not_contains judge；
- cluster precision / recall threshold judge；
- pitfall threshold judge；
- 旧 learning YAML 仍能加载。
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from tests.eval.learning_harness import (
    _trace_event,
    prepare_learning_environment,
    run_learning_scenario,
)
from tests.eval.learning_judge import _pitfall_concept, judge_scenario
from tests.eval.loader import load_scenarios
from tests.eval.mocks import fake_registry, model_response
from tests.eval.scenario import InitialTraceEvent

_DISTILL_UPDATE = {
    "action": "update",
    "proposed_name": None,
    "description": "在修依赖前先确认实际解释器与项目 virtualenv",
    "reason": "多个任务证明 interpreter/virtualenv 错配是稳定问题",
    "procedure": [
        "复现并读 traceback",
        "确认实际解释器与 virtualenv",
        "在项目环境修复依赖",
        "定向与全量 pytest 验证",
    ],
    "pitfalls": [
        "不要在未确认解释器时全局 pip 安装",
        "不要依赖系统 site-packages",
    ],
    "verification": ["定向与全量 pytest 通过"],
    "existing_skill_name": "debug-python",
}

_PY_ALIASES = ("py1", "py2", "py3", "py4", "py5", "py6")


def _scenario() -> object:
    return next(s for s in load_scenarios() if s.id == "learning-10")


def _mining_json(env) -> str:
    task_ids = [env.task_aliases[alias] for alias in _PY_ALIASES]
    return json.dumps(
        {
            "clusters": [
                {
                    "id": "py-env-mismatch",
                    "task_ids": task_ids,
                    "pattern_name": "Python env/interpreter mismatch",
                    "description": "修复 Python 环境/解释器错配导致的测试失败",
                    "similarity_reason": "六个任务都是解释器/virtualenv 错配",
                    "reusable_value": "先确认解释器与 virtualenv 再修依赖",
                }
            ]
        },
        ensure_ascii=False,
    )


async def _run_real(tmp_path: Path):
    """用 fake mining（py1~py6 cluster）+ fake distill（update）跑 learning-10。"""
    scenario = _scenario()
    env = await prepare_learning_environment(
        scenario, root=tmp_path / "l10"
    )
    registry, _ = fake_registry(
        [
            model_response(content=_mining_json(env)),
            model_response(
                content=json.dumps(
                    {"related_skills": ["debug-python"]},
                    ensure_ascii=False,
                )
            ),
            model_response(content=json.dumps(_DISTILL_UPDATE, ensure_ascii=False)),
        ]
    )
    outcome = await run_learning_scenario(
        scenario,
        root=tmp_path / "l10",
        registry=registry,
        environment=env,
    )
    return scenario, env, outcome


# ---------------------------------------------------------------------------
# 1. InitialTraceEvent.step 进入 AgentEvent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initial_trace_event_step_reaches_agent_event(
    tmp_path: Path,
) -> None:
    scenario = _scenario()
    env = await prepare_learning_environment(
        scenario, root=tmp_path / "l10"
    )
    events = await env.trace_store.load_events("py1-r1")
    assert events
    # py1-r1 step1 是 read_file started；step2 是 task_update。
    assert any(e.step == 1 for e in events)
    assert any(
        e.step == 2
        and e.tool_call is not None
        and e.tool_call.name == "task_update"
        for e in events
    )


# ---------------------------------------------------------------------------
# 2. $task:<alias> 递归解析
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_alias_resolved_to_real_task_id(tmp_path: Path) -> None:
    scenario = _scenario()
    env = await prepare_learning_environment(
        scenario, root=tmp_path / "l10"
    )
    py1_id = env.task_aliases["py1"]
    events = await env.trace_store.load_events("py1-r1")
    task_updates = [
        e for e in events if e.tool_call and e.tool_call.name == "task_update"
    ]
    assert task_updates
    for event in task_updates:
        assert event.tool_call.arguments.get("task_id") == py1_id


def test_unknown_task_alias_raises() -> None:
    spec = InitialTraceEvent(
        type="tool_completed",
        tool_name="task_update",
        arguments={"task_id": "$task:ghost"},
    )
    with pytest.raises(ValueError):
        _trace_event("r1", 0, spec, {"py1": "a" * 32})


# ---------------------------------------------------------------------------
# 3. Judge：expected_trace_steps exact
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expected_trace_steps_exact_judge(tmp_path: Path) -> None:
    scenario, _, outcome = await _run_real(tmp_path)
    verdict = judge_scenario(scenario, outcome)
    assert verdict.trace_steps_matched is True
    # 构造不匹配 → exact FAIL。
    bad_steps = {
        **outcome.trace_steps_by_alias,
        "py1": {"py1-r1": (999,)},
    }
    bad = replace(outcome, trace_steps_by_alias=bad_steps)
    verdict_bad = judge_scenario(scenario, bad)
    assert verdict_bad.trace_steps_matched is False
    assert verdict_bad.passed is False
    assert any("trace steps mismatch" in reason for reason in verdict_bad.reasons)


# ---------------------------------------------------------------------------
# 4. Judge：evidence_contains / evidence_not_contains
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evidence_contains_and_not_contains_judge(tmp_path: Path) -> None:
    scenario, _, outcome = await _run_real(tmp_path)
    verdict = judge_scenario(scenario, outcome)
    assert verdict.evidence_missing == ()
    assert verdict.evidence_forbidden == ()
    # 缺关键词 → FAIL。
    bad_missing = replace(
        outcome,
        evidence_by_alias={**outcome.evidence_by_alias, "py1": "无关键词"},
    )
    verdict_missing = judge_scenario(scenario, bad_missing)
    assert verdict_missing.evidence_missing
    assert verdict_missing.passed is False
    # 含禁词 → FAIL（py1 禁词：直接全局安装依赖；py3 禁词：unrelated.txt）。
    bad_forbidden = replace(
        outcome,
        evidence_by_alias={
            **outcome.evidence_by_alias,
            "py3": "unrelated.txt 出现在 span 外",
        },
    )
    verdict_forbidden = judge_scenario(scenario, bad_forbidden)
    assert verdict_forbidden.evidence_forbidden
    assert verdict_forbidden.passed is False


# ---------------------------------------------------------------------------
# 5. Judge：cluster precision / recall threshold
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cluster_threshold_judge(tmp_path: Path) -> None:
    from app.skill_learning import TaskPatternCluster

    scenario, env, outcome = await _run_real(tmp_path)
    verdict = judge_scenario(scenario, outcome)
    # fake cluster = py1~py6，precision/recall = 1.0/1.0 >= 0.8。
    assert verdict.cluster_threshold_passed is True
    # 构造含噪声的 cluster（precision 6/10 = 0.6 < 0.8）→ 阈值 FAIL。
    noisy_ids = [
        env.task_aliases[alias]
        for alias in ("py1", "py2", "py3", "py4", "py5", "py6",
                      "n1", "n2", "n3", "n4")
    ]
    bad_cluster = TaskPatternCluster(
        id="noisy",
        task_ids=tuple(noisy_ids),
        pattern_name="noisy",
        description="d",
        similarity_reason="s",
        reusable_value="r",
    )
    bad_mining = outcome.mining.model_copy(update={"clusters": (bad_cluster,)})
    bad = replace(outcome, mining=bad_mining)
    verdict_bad = judge_scenario(scenario, bad)
    assert verdict_bad.cluster_threshold_passed is False
    assert verdict_bad.passed is False


# ---------------------------------------------------------------------------
# 6. Judge：pitfall threshold
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pitfall_threshold_judge(tmp_path: Path) -> None:
    scenario, _, outcome = await _run_real(tmp_path)
    verdict = judge_scenario(scenario, outcome)
    # fake pitfalls 含 全局 / 解释器 → pitfall_recall 1.0 >= 0.5。
    assert verdict.pitfall_threshold_passed is True
    assert verdict.pitfall_recall == 1.0
    # 构造 pitfalls 为空的 candidate → recall 0 < 0.5 → 阈值 FAIL。
    bad = replace(
        outcome,
        candidates=tuple(
            candidate.model_copy(update={"pitfalls": ()})
            for candidate in outcome.candidates
        ),
    )
    verdict_bad = judge_scenario(scenario, bad)
    assert verdict_bad.pitfall_recall == 0.0
    assert verdict_bad.pitfall_threshold_passed is False
    assert verdict_bad.passed is False


# ---------------------------------------------------------------------------
# 7. 完整 run + 旧场景兼容
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_learning10_full_run_judge(tmp_path: Path) -> None:
    scenario, _, outcome = await _run_real(tmp_path)
    assert outcome.mining is not None and outcome.mining.cluster_count == 1
    assert len(outcome.candidates) == 1
    candidate = outcome.candidates[0]
    assert candidate.action.value == "update"
    assert candidate.existing_skill_name == "debug-python"
    verdict = judge_scenario(scenario, outcome)
    assert verdict.passed is True


def test_old_learning_yamls_still_load() -> None:
    scenarios = load_scenarios()
    old_ids = {
        "learning-01",
        "learning-02",
        "learning-03",
        "learning-04",
        "learning-05a",
        "learning-05b",
        "learning-05c",
        "learning-06",
        "learning-07",
        "learning-08",
        "learning-09",
    }
    ids = {s.id for s in scenarios}
    assert old_ids <= ids
    assert "learning-10" in ids


# ---------------------------------------------------------------------------
# 8. Pitfall 同义组（中英 alias）judge：concept-based recall
# ---------------------------------------------------------------------------


def test_pitfall_concept_normalization() -> None:
    # 旧格式单字符串 == [该字符串]
    assert _pitfall_concept("解释器") == ("解释器",)
    # list / tuple 视为同一 concept 的 alias 组
    assert _pitfall_concept(("全局", "global")) == ("全局", "global")
    assert _pitfall_concept(["解释器", "interpreter"]) == ("解释器", "interpreter")


@pytest.mark.asyncio
async def test_pitfall_alias_group_english_synonym_hits(tmp_path: Path) -> None:
    # learning-10 期望是 [[全局, global], [解释器, interpreter]]。
    # 模型 pitfalls 全用英文 synonym → 组内任一 alias 命中即算 concept 命中。
    scenario, _, outcome = await _run_real(tmp_path)
    cand = outcome.candidates[0]
    bad = replace(
        outcome,
        candidates=(
            cand.model_copy(
                update={
                    "pitfalls": (
                        "never pip install into the global environment",
                        "always verify the actual interpreter before fixing",
                    )
                }
            ),
        ),
    )
    verdict = judge_scenario(scenario, bad)
    assert verdict.pitfall_recall == 1.0
    assert verdict.pitfall_threshold_passed is True
    assert verdict.passed is True


@pytest.mark.asyncio
async def test_pitfall_alias_group_partial_hit(tmp_path: Path) -> None:
    # 只命中一个 concept（global）→ recall 0.5 == min_pitfall_recall → 恰好过。
    scenario, _, outcome = await _run_real(tmp_path)
    cand = outcome.candidates[0]
    bad = replace(
        outcome,
        candidates=(
            cand.model_copy(
                update={"pitfalls": ("always use the global pip", "lint only")}
            ),
        ),
    )
    verdict = judge_scenario(scenario, bad)
    assert verdict.pitfall_recall == 0.5
    assert verdict.pitfall_threshold_passed is True


@pytest.mark.asyncio
async def test_pitfall_alias_group_total_miss(tmp_path: Path) -> None:
    # 两个 concept 都没命中 → recall 0.0 → 阈值 FAIL。
    scenario, _, outcome = await _run_real(tmp_path)
    cand = outcome.candidates[0]
    bad = replace(
        outcome,
        candidates=(cand.model_copy(update={"pitfalls": ("lint errors",)}),),
    )
    verdict = judge_scenario(scenario, bad)
    assert verdict.pitfall_recall == 0.0
    assert verdict.pitfall_threshold_passed is False
    assert verdict.passed is False


@pytest.mark.asyncio
async def test_pitfall_old_single_string_format_still_works(tmp_path: Path) -> None:
    # 旧场景（expected_pitfall_keywords 是单字符串）行为不变：中文 substring 命中。
    scenario, _, outcome = await _run_real(tmp_path)
    old_expect = scenario.expect.learning.model_copy(
        update={
            "expected_pitfall_keywords": ("全局", "解释器"),
        }
    )
    scenario_old = scenario.model_copy(
        update={"expect": scenario.expect.model_copy(update={"learning": old_expect})}
    )
    cand = outcome.candidates[0]
    hit = replace(
        outcome,
        candidates=(
            cand.model_copy(
                update={
                    "pitfalls": ("不要在未确认解释器时全局 pip 安装",),
                }
            ),
        ),
    )
    verdict = judge_scenario(scenario_old, hit)
    assert verdict.pitfall_recall == 1.0
    assert verdict.pitfall_threshold_passed is True
