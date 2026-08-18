"""Harness 自检：用 Mock 模型验证加载、运行、预置与评分机制（不调真实模型）。"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.agent.events import InMemoryEventHandler
from tests.eval import harness
from tests.eval.assertions import run_checks
from tests.eval.loader import load_scenarios
from tests.eval.metrics import (
    EvalReport,
    ScenarioMetric,
    metric_from_outcome,
    render_report,
)
from tests.eval.mocks import (
    fake_registry,
    history_messages,
    model_response,
    text_tool_call,
)
from tests.eval.scenario import Scenario


@pytest.fixture
def scenarios() -> tuple[Scenario, ...]:
    return load_scenarios()


def _by_id(scenarios: tuple[Scenario, ...], scenario_id: str) -> Scenario:
    return next(scenario for scenario in scenarios if scenario.id == scenario_id)


async def _run(scenario: Scenario, tmp_path, responses):
    registry, _ = fake_registry(responses)
    outcome = await harness.run_scenario(
        scenario,
        root=tmp_path / scenario.id,
        provider="fake",
        registry=registry,
    )
    checks, passed = await run_checks(scenario, outcome=outcome)
    return outcome, checks, passed


def test_loads_all_starting_scenarios(scenarios: tuple[Scenario, ...]) -> None:
    assert len(scenarios) == 54
    ids = [scenario.id for scenario in scenarios]
    assert len(set(ids)) == 54
    base_ids = {f"eval-{index:02d}" for index in range(1, 31)}
    skill_ids = {f"skill-{index:02d}" for index in range(1, 16)}
    learning_ids = {f"learning-{index:02d}" for index in range(1, 10)}
    assert set(ids) == base_ids | skill_ids | learning_ids
    groups = {scenario.group for scenario in scenarios}
    assert groups == {
        "basic",
        "tools",
        "task",
        "context",
        "safety",
        "skill",
        "learning",
    }
    # 每个基础分组 6 条，skill 组 15 条，learning 组 9 条。
    from collections import Counter

    counts = Counter(scenario.group for scenario in scenarios)
    assert counts == {
        "basic": 6,
        "tools": 6,
        "task": 6,
        "context": 6,
        "safety": 6,
        "skill": 15,
        "learning": 9,
    }


async def test_eval01_simple_qa_creates_no_task(
    scenarios: tuple[Scenario, ...],
    tmp_path,
) -> None:
    scenario = _by_id(scenarios, "eval-01")
    outcome, checks, passed = await _run(
        scenario,
        tmp_path,
        [model_response(content="2 + 3 等于 5。")],
    )
    assert passed is True, [(check.name, check.detail) for check in checks]
    assert outcome.result is not None and outcome.result.steps == 1
    tasks = await outcome.environment.task_store.list()
    assert tasks == ()


async def test_eval02_read_file_tool_works(
    scenarios: tuple[Scenario, ...],
    tmp_path,
) -> None:
    scenario = _by_id(scenarios, "eval-02")
    # 模型第一步读取文件，第二步总结。
    outcome, checks, passed = await _run(
        scenario,
        tmp_path,
        [
            model_response(
                tool_calls=(
                    text_tool_call(
                        "read-1",
                        "read_file",
                        {"path": "notes/intro.txt"},
                    ),
                )
            ),
            model_response(content="文件内容是：OneAgent 是一个本地智能助手。"),
        ],
    )
    assert passed is True
    tools_check = next(check for check in checks if check.name == "tools")
    assert tools_check.ok is True
    answer_check = next(check for check in checks if check.name == "answer")
    assert answer_check.ok is True


async def test_eval04_complex_request_creates_task(
    scenarios: tuple[Scenario, ...],
    tmp_path,
) -> None:
    scenario = _by_id(scenarios, "eval-04")
    outcome, checks, passed = await _run(
        scenario,
        tmp_path,
        [
            model_response(
                tool_calls=(
                    text_tool_call(
                        "create-1",
                        "task_create",
                        {
                            "title": "完成三个开发事项",
                            "goal": "完成 API、前端和 MCP 三件事",
                            "steps": [
                                {"title": "搭建 API 层"},
                                {"title": "编写前端聊天页"},
                                {"title": "调研 MCP 集成方案"},
                            ],
                        },
                    ),
                )
            ),
            model_response(content="已记录任务并开始规划。"),
        ],
    )
    assert passed is True
    task_check = next(check for check in checks if check.name == "task")
    assert task_check.ok is True
    tasks = await outcome.environment.task_store.list()
    assert len(tasks) == 1
    assert tasks[0].owner_conversation_id == harness.DEFAULT_CONVERSATION_ID


async def test_existing_task_is_not_mistaken_for_new_task(tmp_path) -> None:
    """created=false 表示本轮不新增，而不是运行后 Task 总数为零。"""

    scenario = Scenario.model_validate(
        {
            "id": "task-baseline",
            "group": "task",
            "name": "预置任务基线",
            "initial_tasks": [
                {
                    "alias": "current",
                    "title": "已有任务",
                    "goal": "保持既有目标",
                }
            ],
            "user_input": "告诉我当前目标。",
            "allowed_tools": [],
            "expect": {
                "task": {
                    "created": False,
                    "new_count": 0,
                    "target": "current",
                    "goal_contains": ["既有目标"],
                },
                "answer": {"keypoints": ["既有目标"]},
            },
        }
    )
    _, checks, passed = await _run(
        scenario,
        tmp_path,
        [model_response(content="当前需要保持既有目标。")],
    )
    assert passed is True
    task_check = next(check for check in checks if check.name == "task")
    assert task_check.ok is True


async def test_eval03_checks_failed_tool_and_blocked_step(
    scenarios: tuple[Scenario, ...],
    tmp_path,
) -> None:
    """失败场景必须执行失败工具，并把原步骤写成带说明的 blocked。"""

    scenario = _by_id(scenarios, "eval-03")
    environment = await harness.prepare_environment(
        scenario,
        root=tmp_path / "eval-03",
    )
    task_id = environment.initial_task_ids[0]
    registry, _ = fake_registry(
        [
            model_response(
                tool_calls=(
                    text_tool_call(
                        "read-1",
                        "read_file",
                        {"path": "data/report.csv"},
                    ),
                )
            ),
            model_response(
                tool_calls=(
                    text_tool_call(
                        "update-1",
                        "task_update",
                        {
                            "task_id": task_id,
                            "step_id": "s1",
                            "step_status": "blocked",
                            "step_note": "data/report.csv 不存在，无法继续读取。",
                        },
                    ),
                )
            ),
            model_response(content="文件不存在，读取失败，任务已标记为阻塞。"),
        ]
    )
    runtime = harness.build_runtime(
        scenario,
        environment,
        provider="fake",
        registry=registry,
    )
    handler = InMemoryEventHandler()
    result = await runtime.run(
        scenario.user_input,
        history=history_messages(scenario.initial_history),
        conversation_id=environment.conversation_id,
        event_handler=handler,
    )
    outcome = harness.EvalOutcome(
        scenario=scenario,
        environment=environment,
        result=result,
        events=list(handler.events),
    )
    checks, passed = await run_checks(scenario, outcome=outcome)
    assert passed is True, [(check.name, check.detail) for check in checks]


async def test_eval05_really_compacts_and_keeps_goal(
    scenarios: tuple[Scenario, ...],
    tmp_path,
) -> None:
    scenario = _by_id(scenarios, "eval-05")
    summary = {
        "current_objective": "编写产品说明",
        "user_constraints": ["只围绕效率提升这一核心卖点"],
        "key_decisions": [],
        "completed_work": ["已完成引言和功能特性"],
        "current_state": ["正在继续完善说明"],
        "pending_work": ["说明下一步工作"],
        "important_facts": ["核心目标是效率提升"],
    }
    outcome, checks, passed = await _run(
        scenario,
        tmp_path,
        [
            model_response(content=json.dumps(summary, ensure_ascii=False)),
            model_response(content="当前核心目标是效率提升，下一步继续完善说明。"),
        ],
    )
    assert passed is True, [(check.name, check.detail) for check in checks]
    assert outcome.result is not None
    compaction = next(check for check in checks if check.name == "compaction")
    assert compaction.ok is True
    assert outcome.result.summary_state is not None


def test_rejects_contradictory_scenario() -> None:
    with pytest.raises(ValidationError):
        Scenario.model_validate(
            {
                "id": "invalid",
                "name": "冲突场景",
                "user_input": "test",
                "expect": {
                    "tools": {
                        "must": ["read_file"],
                        "must_not": ["read_file"],
                    }
                },
            }
        )


async def test_model_error_does_not_count_as_success(tmp_path) -> None:
    scenario = Scenario.model_validate(
        {
            "id": "model-error",
            "name": "模型错误",
            "user_input": "test",
            "allowed_tools": [],
            "expect": {},
        }
    )
    _, checks, passed = await _run(
        scenario,
        tmp_path,
        [RuntimeError("offline model failure")],
    )
    assert passed is False
    ran_ok = next(check for check in checks if check.name == "ran_ok")
    assert ran_ok.ok is False


def test_metric_rate_excludes_non_applicable_checks() -> None:
    report = EvalReport(
        metrics=[
            ScenarioMetric(
                scenario_id="irrelevant",
                group="basic",
                name="无工具期望",
                passed=True,
                checks=[
                    harness_check("tools", True, applicable=False),
                ],
            ),
            ScenarioMetric(
                scenario_id="tool-case",
                group="tools",
                name="工具失败",
                passed=False,
                checks=[harness_check("tools", False)],
            ),
        ]
    )
    assert report.tool_selection_rate == 0.0


def harness_check(name: str, ok: bool, *, applicable: bool = True):
    from tests.eval.assertions import CheckResult

    return CheckResult(name, ok, applicable=applicable)


async def test_rejects_initial_file_path_traversal(tmp_path) -> None:
    scenario = Scenario.model_validate(
        {
            "id": "unsafe-path",
            "name": "路径穿越",
            "user_input": "test",
            "initial_files": [{"path": "../escape.txt", "content": "x"}],
            "expect": {},
        }
    )
    with pytest.raises(ValueError, match="超出 workspace"):
        await harness.prepare_environment(scenario, root=tmp_path)


async def test_eval06_approval_deny_never_succeeds(
    scenarios: tuple[Scenario, ...],
    tmp_path,
) -> None:
    scenario = _by_id(scenarios, "eval-06")
    outcome, checks, passed = await _run(
        scenario,
        tmp_path,
        [
            model_response(
                tool_calls=(
                    text_tool_call(
                        "shell-1",
                        "run_shell_command",
                        {"command": "echo hello"},
                    ),
                )
            ),
            model_response(content="该命令被拒绝，无法执行。"),
        ],
    )
    assert passed is True
    tools_check = next(check for check in checks if check.name == "tools")
    assert tools_check.ok is True
    records = outcome.result.tool_calls
    assert len(records) == 1
    assert records[0].result.success is False  # 审批拒绝 → 未成功执行


async def test_metrics_render_report(
    scenarios: tuple[Scenario, ...],
    tmp_path,
) -> None:
    registry, _ = fake_registry([model_response(content="ok")])
    scenario = _by_id(scenarios, "eval-01")
    outcome = await harness.run_scenario(
        scenario,
        root=tmp_path / "report",
        provider="fake",
        registry=registry,
    )
    checks, passed = await run_checks(scenario, outcome=outcome)
    report = EvalReport()
    report.metrics.append(
        metric_from_outcome(scenario, outcome, checks, passed)
    )
    text = render_report(report)
    assert "OneAgent Eval Report" in text
    assert "样本通过率" in text
    assert "唯一场景数" in text
    assert scenario.id in text
