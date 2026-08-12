"""长期记忆多阶段 Eval Harness 的离线测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.models.types import ToolCall
from tests.eval.mocks import fake_registry, model_response
from tests.memory_eval.assertions import check_phase
from tests.memory_eval.harness import run_scenario
from tests.memory_eval.loader import load_scenarios
from tests.memory_eval.metrics import (
    MemoryEvalReport,
    metric_from_phase,
    render_report,
)
from tests.memory_eval.scenario import MemoryEvalScenario


def _scenario() -> MemoryEvalScenario:
    return MemoryEvalScenario.model_validate(
        {
            "id": "offline-cross-session",
            "name": "离线跨会话闭环",
            "phases": [
                {
                    "id": "learn",
                    "conversation": "A",
                    "user_input": "记住项目使用 Markdown Memory。",
                    "bind_reflection_memory_as": "design",
                    "expect": {
                        "reflection_action": "create",
                        "active_count": 1,
                        "memory": {
                            "target": "design",
                            "content_contains": ["Markdown"],
                        },
                    },
                },
                {
                    "id": "recall",
                    "conversation": "B",
                    "user_input": "项目采用什么记忆方案？",
                    "expect": {
                        "recalled": ["design"],
                        "total_memory_reads": 1,
                        "answer": {"contains": ["Markdown"]},
                    },
                },
            ],
        }
    )


@pytest.mark.asyncio
async def test_multiphase_harness_shares_memory_but_isolates_history(
    tmp_path: Path,
) -> None:
    responses = [
        model_response(content="已记录。"),
        model_response(
            content=(
                '{"action":"create","memory_id":null,'
                '"title":"项目记忆方案","summary":"项目使用 Markdown Memory",'
                '"content":"项目使用 Markdown Memory。",'
                '"reason":"稳定的跨会话项目决定"}'
            )
        ),
        model_response(
            tool_calls=(
                ToolCall(
                    id="read-1",
                    name="memory_read",
                    arguments={"memory_id": "M001"},
                ),
            )
        ),
        model_response(content="项目使用 Markdown Memory。"),
        model_response(
            content=(
                '{"action":"update","memory_id":"M001",'
                '"title":"项目记忆方案","summary":"项目使用 Markdown Memory",'
                '"content":"项目使用 Markdown Memory。",'
                '"reason":"本轮只是成功召回，没有新增事实"}'
            )
        ),
    ]
    registry, adapter = fake_registry(responses)
    scenario = _scenario()

    outcome = await run_scenario(
        scenario,
        root=tmp_path,
        provider="fake",
        model="fake-model",
        registry=registry,
    )

    assert len(outcome.phases) == 2
    assert outcome.aliases["design"] == "M001"
    assert outcome.phases[0].result is not None
    assert outcome.phases[1].result is not None
    first_phase_second_request = adapter.requests[1]
    second_phase_first_request = adapter.requests[2]
    assert any(
        "记住项目使用 Markdown" in (message.content or "")
        for message in first_phase_second_request.messages
    )
    assert not any(
        message.role.value == "user"
        and "记住项目使用 Markdown" in (message.content or "")
        for message in second_phase_first_request.messages
    )
    for phase in outcome.phases:
        checks, passed = check_phase(scenario, phase, aliases=outcome.aliases)
        assert passed, checks
    learn_artifact = tmp_path / "artifacts" / "learn.json"
    recall_artifact = tmp_path / "artifacts" / "recall.json"
    assert learn_artifact.is_file()
    assert recall_artifact.is_file()
    artifact_text = learn_artifact.read_text(encoding="utf-8")
    artifact = json.loads(artifact_text)
    assert '"raw_output"' in artifact_text
    assert json.loads(artifact["reflection"]["raw_output"])["action"] == "create"
    assert "记住项目使用 Markdown" in artifact_text


def test_loader_contains_ten_unique_baseline_scenarios() -> None:
    scenarios = load_scenarios()

    assert len(scenarios) == 10
    assert len({scenario.id for scenario in scenarios}) == 10
    assert {"recall", "reflection", "maintenance", "routing"} <= {
        tag for scenario in scenarios for tag in scenario.tags
    }


def test_scenario_rejects_duplicate_aliases() -> None:
    with pytest.raises(ValueError, match="aliases"):
        MemoryEvalScenario.model_validate(
            {
                "id": "duplicate",
                "name": "重复别名",
                "initial_memories": [
                    {
                        "alias": "same",
                        "title": "A",
                        "summary": "A",
                        "content": "A",
                    }
                ],
                "phases": [
                    {
                        "id": "p1",
                        "user_input": "x",
                        "bind_reflection_memory_as": "same",
                    }
                ],
            }
        )


def test_report_separates_main_reflection_and_maintenance_usage() -> None:
    report = MemoryEvalReport(provider="fake", model="fake-model")
    scenario = _scenario()
    phase = scenario.phases[0]
    from tests.memory_eval.harness import MemoryEvalPhaseOutcome  # noqa: PLC0415

    metric = metric_from_phase(
        scenario.id,
        scenario.name,
        MemoryEvalPhaseOutcome(phase=phase),
        [],
        False,
        run=1,
        mode="on",
    )
    report.metrics.append(metric)

    rendered = render_report(report)

    assert "Main tokens" in rendered
    assert "Reflection tokens" in rendered
    assert "Maintenance tokens" in rendered
    assert "offline-cross-session" in rendered
