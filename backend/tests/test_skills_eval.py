"""Skill Eval 的离线冒烟测试。

用 Mock 模型驱动 Harness，验证 skill 场景的装配（skill 目录预置、
skill 工具注册、runtime 注入）与 skill 断言逻辑，不调用真实模型。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.events import AgentEventType
from tests.eval.assertions import _check_skill, run_checks
from tests.eval.harness import run_scenario
from tests.eval.loader import load_scenarios
from tests.eval.mocks import fake_registry, model_response, text_tool_call


def _skill_scenario(scenario_id: str):
    scenarios = load_scenarios()
    return next(s for s in scenarios if s.id == scenario_id)


@pytest.mark.asyncio
async def test_eval_skill_activation_flow(tmp_path: Path) -> None:
    scenario = _skill_scenario("skill-01")
    registry, _ = fake_registry(
        [
            model_response(
                tool_calls=(
                    text_tool_call(
                        "c1",
                        "skill_read",
                        {"name": "debug-python"},
                    ),
                )
            ),
            model_response(content="已按流程排查：复现 → 读 traceback → 修复。"),
        ]
    )
    outcome = await run_scenario(
        scenario,
        root=tmp_path,
        provider="fake",
        registry=registry,
    )

    assert outcome.error is None
    activated = [
        e for e in outcome.events if e.type is AgentEventType.SKILL_ACTIVATED
    ]
    assert len(activated) == 1
    assert activated[0].skill_name == "debug-python"

    checks, ok = await run_checks(scenario, outcome=outcome)
    skill_check = next(c for c in checks if c.name == "skill")
    assert skill_check.ok, skill_check.detail


@pytest.mark.asyncio
async def test_eval_skill_not_activated_flow(tmp_path: Path) -> None:
    scenario = _skill_scenario("skill-07")
    registry, _ = fake_registry(
        [
            model_response(
                tool_calls=(
                    text_tool_call(
                        "w1",
                        "write_file",
                        {"path": "notes/todo.md", "content": "买菜\n写周报\n健身"},
                    ),
                )
            ),
            model_response(content="已创建待办文件。"),
        ]
    )
    outcome = await run_scenario(
        scenario,
        root=tmp_path,
        provider="fake",
        registry=registry,
    )

    assert outcome.error is None
    assert not any(
        e.type is AgentEventType.SKILL_ACTIVATED for e in outcome.events
    )
    checks, ok = await run_checks(scenario, outcome=outcome)
    skill_check = next(c for c in checks if c.name == "skill")
    assert skill_check.ok, skill_check.detail


def test_skill_check_survives_compaction(tmp_path: Path) -> None:
    """直接验证断言：压缩后实际 ModelRequest 仍注入 active skill message 才算通过。"""

    scenario = _skill_scenario("skill-15")
    from app.agent.events import AgentEvent

    def started_event(sequence: int, stage: str, active_messages: tuple[str, ...]):
        return AgentEvent(
            run_id="r",
            conversation_id="c",
            sequence=sequence,
            type=AgentEventType.MODEL_STARTED,
            compaction_stage=stage,
            active_skill_message_names=active_messages,
        )

    activated_event = AgentEvent(
        run_id="r",
        conversation_id="c",
        sequence=1,
        type=AgentEventType.SKILL_ACTIVATED,
        skill_name="debug-python",
    )

    # 压缩后实际请求仍注入 active skill message → 通过。
    events = [
        started_event(0, "none", ()),
        activated_event,
        started_event(1, "compact", ()),
        started_event(2, "none", ("debug-python",)),
    ]
    check = _check_skill(scenario, events)
    assert check.ok, check.detail

    # 压缩后 run state 还在（active_skill_names）但实际消息未注入 → 失败。
    events_state_only = [
        started_event(0, "none", ()),
        activated_event,
        started_event(1, "compact", ()),
        AgentEvent(
            run_id="r",
            conversation_id="c",
            sequence=2,
            type=AgentEventType.MODEL_STARTED,
            compaction_stage="none",
            active_skill_names=("debug-python",),
            active_skill_message_names=(),
        ),
    ]
    check_state_only = _check_skill(scenario, events_state_only)
    assert not check_state_only.ok

    # 压缩后 active skill 消息丢失 → 失败。
    events_lost = [
        started_event(0, "none", ()),
        activated_event,
        started_event(1, "compact", ()),
        started_event(2, "none", ()),
    ]
    check_lost = _check_skill(scenario, events_lost)
    assert not check_lost.ok
    assert "active skill 未在压缩后保留" in check_lost.detail


@pytest.mark.asyncio
async def test_active_skill_message_in_real_requests(tmp_path: Path) -> None:
    """验证实际 ModelRequest 中真的注入了 Active Skill message。

    不依赖事件字段：直接检查 FakeModelAdapter 捕获的真实 ModelRequest。
    skill-01 场景激活 debug-python 后，激活之后的每一步请求都必须包含
    vesta_active_skill 且正文含目标 Skill 名。压缩只会压缩历史消息，
    Active 指令作为 ephemeral 每 Step 重建，不会因压缩丢失；
    “压缩后仍注入”的判定由 test_skill_check_survives_compaction 覆盖。
    """

    scenario = _skill_scenario("skill-01")
    registry, adapter = fake_registry(
        [
            model_response(
                tool_calls=(
                    text_tool_call(
                        "c1",
                        "skill_read",
                        {"name": "debug-python"},
                    ),
                )
            ),
            model_response(content="已按流程排查：复现 → 读 traceback → 修复。"),
        ]
    )
    outcome = await run_scenario(
        scenario,
        root=tmp_path,
        provider="fake",
        registry=registry,
    )

    assert outcome.error is None
    assert any(
        e.type is AgentEventType.SKILL_ACTIVATED for e in outcome.events
    )

    # 找到激活发生的 step（SKILL_ACTIVATED 事件）。
    activated_event = next(
        e for e in outcome.events if e.type is AgentEventType.SKILL_ACTIVATED
    )
    activated_step = activated_event.step or 1

    # 激活之后（激活发生在工具轮之后，下一 step 起）的每个实际请求都必须
    # 包含 Active Skill message，且正文含目标 Skill 名。
    active_message_seen = False
    for request in adapter.requests[activated_step:]:
        active_msgs = [
            m
            for m in request.messages
            if getattr(m, "name", None) == "vesta_active_skill"
        ]
        assert active_msgs, "激活后的请求缺少 vesta_active_skill message"
        for message in active_msgs:
            active_message_seen = True
            assert "debug-python" in (message.content or "")
    assert active_message_seen
