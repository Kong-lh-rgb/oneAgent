"""Skill Learning Eval 的离线测试。

用 Fake 模型驱动 learning_harness，验证 learning-01..08 场景的状态机：
无候选 / CREATE / UPDATE / 机械操作不沉淀 / pending 不可见 / accept / reject。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.eval.learning_harness import (
    prepare_learning_environment,
    run_learning_scenario,
)
from tests.eval.loader import load_scenarios
from tests.eval.mocks import fake_registry, model_response

# 供 distillation 复用的 candidate 响应模板（按场景覆盖 action / name / pitfalls）。
_DISTILL_CREATE = {
    "action": "create",
    "proposed_name": "python-runtime-debug",
    "description": "排查 Python 运行时错误的标准流程",
    "reason": "多个相似任务证明该流程稳定",
    "procedure": ["复现", "读 traceback", "定位根因", "修复", "跑 pytest"],
    "pitfalls": ["不要跳过复现", "不要直接改依赖前先确认 virtualenv"],
    "verification": ["pytest 通过", "启动命令恢复正常"],
}
_DISTILL_CI = {
    "action": "create",
    "proposed_name": "ci-cache-recovery",
    "description": "恢复被依赖缓存卡住的 CI 构建",
    "reason": "多次出现相同缓存锁失败后清理缓存成功",
    "procedure": ["查看失败日志", "清理缓存", "重试构建"],
    "pitfalls": ["不要盲目升级依赖版本", "不要直接重跑不清理缓存"],
    "verification": ["CI 全绿"],
}
_DISTILL_UPDATE = {
    "action": "update",
    "proposed_name": None,
    "description": "在排查前先确认 virtualenv",
    "reason": "最近多个任务都证明应先确认 virtualenv",
    "procedure": ["复现", "确认 virtualenv", "读 traceback", "修复", "验证"],
    "pitfalls": ["不要跳过 virtualenv 确认"],
    "verification": ["pytest 通过"],
    "existing_skill_name": "debug-python",
}


def _learning_scenario(scenario_id: str):
    scenarios = load_scenarios()
    return next(s for s in scenarios if s.id == scenario_id)


def _mining_response(task_ids: tuple[str, ...], *, clusters: bool) -> str:
    if not clusters:
        return json.dumps({"clusters": []}, ensure_ascii=False)
    return json.dumps(
        {
            "clusters": [
                {
                    "id": "pattern-1",
                    "task_ids": list(task_ids[:3]),
                    "pattern_name": "Python runtime debugging",
                    "description": "修复 Python 运行时错误",
                    "similarity_reason": "均为 Python 报错排查",
                    "reusable_value": "多步骤流程可复用",
                }
            ]
        },
        ensure_ascii=False,
    )


def _registry_for(
    task_ids: tuple[str, ...],
    *,
    clusters: bool,
    distill: dict,
):
    responses = [
        model_response(content=_mining_response(task_ids, clusters=clusters)),
        model_response(content=json.dumps(distill, ensure_ascii=False)),
    ]
    return fake_registry(responses)


@pytest.mark.asyncio
async def test_learning_01_unrelated_tasks_no_candidate(tmp_path: Path) -> None:
    scenario = _learning_scenario("learning-01")
    registry, _ = _registry_for((), clusters=False, distill={})
    outcome = await run_learning_scenario(
        scenario,
        root=tmp_path / "l01",
        registry=registry,
    )
    assert outcome.error is None
    assert outcome.candidates == ()
    assert outcome.mining is not None and outcome.mining.cluster_count == 0


@pytest.mark.asyncio
async def test_learning_02_similar_tasks_create_candidate(tmp_path: Path) -> None:
    scenario = _learning_scenario("learning-02")
    env = await prepare_learning_environment(scenario, root=tmp_path / "l02")
    registry, _ = _registry_for(env.task_ids, clusters=True, distill=_DISTILL_CREATE)
    outcome = await run_learning_scenario(
        scenario,
        root=tmp_path / "l02",
        registry=registry,
        environment=env,
    )
    assert outcome.error is None
    assert len(outcome.candidates) == 1
    candidate = outcome.candidates[0]
    assert candidate.proposed_name == "python-runtime-debug"
    assert set(candidate.source_task_ids).issubset(set(env.task_ids))
    assert candidate.source_run_ids
    assert candidate.reason
    assert candidate.procedure
    assert outcome.created_skills == ()
    # pending：正式 Skill 尚不可见。
    assert await env.skill_store.load("python-runtime-debug") is None


@pytest.mark.asyncio
async def test_learning_03_mechanical_rename_no_candidate(tmp_path: Path) -> None:
    scenario = _learning_scenario("learning-03")
    registry, _ = _registry_for((), clusters=False, distill={})
    outcome = await run_learning_scenario(
        scenario,
        root=tmp_path / "l03",
        registry=registry,
    )
    assert outcome.error is None
    assert outcome.candidates == ()


@pytest.mark.asyncio
async def test_learning_04_failure_then_success_pitfalls(tmp_path: Path) -> None:
    scenario = _learning_scenario("learning-04")
    env = await prepare_learning_environment(scenario, root=tmp_path / "l04")
    registry, _ = _registry_for(env.task_ids, clusters=True, distill=_DISTILL_CI)
    outcome = await run_learning_scenario(
        scenario,
        root=tmp_path / "l04",
        registry=registry,
        environment=env,
    )
    assert outcome.error is None
    assert len(outcome.candidates) == 1
    candidate = outcome.candidates[0]
    assert candidate.proposed_name == "ci-cache-recovery"
    assert candidate.pitfalls  # 稳定失败 → 应避免做法被沉淀


@pytest.mark.asyncio
async def test_learning_05_existing_skill_update(tmp_path: Path) -> None:
    scenario = _learning_scenario("learning-05")
    env = await prepare_learning_environment(scenario, root=tmp_path / "l05")
    registry, _ = _registry_for(env.task_ids, clusters=True, distill=_DISTILL_UPDATE)
    outcome = await run_learning_scenario(
        scenario,
        root=tmp_path / "l05",
        registry=registry,
        environment=env,
    )
    assert outcome.error is None
    assert len(outcome.candidates) == 1
    candidate = outcome.candidates[0]
    assert candidate.action.value == "update"
    assert candidate.existing_skill_name == "debug-python"
    assert candidate.proposed_name == "debug-python"


@pytest.mark.asyncio
async def test_learning_06_pending_not_visible(tmp_path: Path) -> None:
    scenario = _learning_scenario("learning-06")
    env = await prepare_learning_environment(scenario, root=tmp_path / "l06")
    registry, _ = _registry_for(env.task_ids, clusters=True, distill=_DISTILL_CREATE)
    outcome = await run_learning_scenario(
        scenario,
        root=tmp_path / "l06",
        registry=registry,
        environment=env,
    )
    assert outcome.error is None
    assert len(outcome.candidates) == 1
    # 未 accept → SkillStore 不可见。
    catalog = await env.skill_store.catalog()
    assert all(item.name != "python-runtime-debug" for item in catalog)
    assert await env.skill_store.load("python-runtime-debug") is None


@pytest.mark.asyncio
async def test_learning_07_accept_creates_skill(tmp_path: Path) -> None:
    scenario = _learning_scenario("learning-07")
    env = await prepare_learning_environment(scenario, root=tmp_path / "l07")
    registry, _ = _registry_for(env.task_ids, clusters=True, distill=_DISTILL_CREATE)
    outcome = await run_learning_scenario(
        scenario,
        root=tmp_path / "l07",
        registry=registry,
        environment=env,
        accept_names=("python-runtime-debug",),
    )
    assert outcome.error is None
    assert outcome.created_skills == ("python-runtime-debug",)
    skill = await env.skill_store.load("python-runtime-debug")
    assert skill is not None
    assert "读 traceback" in skill.content
    catalog = await env.skill_store.catalog()
    assert any(item.name == "python-runtime-debug" for item in catalog)


@pytest.mark.asyncio
async def test_learning_08_reject_no_skill(tmp_path: Path) -> None:
    scenario = _learning_scenario("learning-08")
    env = await prepare_learning_environment(scenario, root=tmp_path / "l08")
    registry, _ = _registry_for(env.task_ids, clusters=True, distill=_DISTILL_CREATE)
    outcome = await run_learning_scenario(
        scenario,
        root=tmp_path / "l08",
        registry=registry,
        environment=env,
        reject_names=("python-runtime-debug",),
    )
    assert outcome.error is None
    assert outcome.created_skills == ()
    # reject 后不产生正式 Skill。
    assert await env.skill_store.load("python-runtime-debug") is None
    assert outcome.candidates[0].status.value == "rejected"
