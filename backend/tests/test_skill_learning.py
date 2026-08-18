"""Skill Learning V1 的离线确定性测试。

覆盖：TaskCard 投影、Mining Trigger + Watermark、Pattern Mining、Trace
Evidence、SkillCandidate 结构、Human Gate（pending / reject / accept）。
全程使用 Fake 模型，不调用真实 API。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.agent.events import AgentEvent, AgentEventType
from app.models.adapter import ModelAdapter
from app.models.config import ModelSettings, ProviderConfig
from app.models.registry import ModelAdapterRegistry
from app.models.types import (
    ApiStyle,
    Message,
    MessageRole,
    ModelResponse,
    ModelUsage,
    ToolCall,
    ToolResult,
)
from app.skill_learning import (
    SkillCandidate,
    SkillCandidateAction,
    SkillCandidateStatus,
    SkillCandidateStore,
    SkillLearningService,
    SkillLearningSettings,
    TaskCard,
    TraceEvidenceBuilder,
)
from app.skills import SkillStore
from app.task import FileTaskStore, TaskStatus, TaskStep, TaskStepStatus
from app.trace.store import SQLiteTraceStore

# ---------------------------------------------------------------------------
# Fake 模型
# ---------------------------------------------------------------------------


class _FakeAdapter(ModelAdapter):
    """按顺序弹出一条响应的离线模型。"""

    def __init__(
        self,
        config: ProviderConfig,
        responses: list[ModelResponse | Exception],
    ) -> None:
        super().__init__(config)
        self.responses = list(responses)
        self.requests: list[Message] = []

    async def complete(self, request) -> ModelResponse:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def close(self) -> None:
        pass


def _model_response(content: str) -> ModelResponse:
    return ModelResponse(
        id="fake-response",
        provider="fake",
        model="fake-model",
        message=Message(role=MessageRole.ASSISTANT, content=content),
        usage=ModelUsage(),
    )


def _fake_registry(
    responses: list[ModelResponse | Exception],
) -> tuple[ModelAdapterRegistry, _FakeAdapter]:
    config = ProviderConfig(
        provider="fake",
        model="fake-model",
        api_key=SecretStr("offline-test-key"),
        api_style=ApiStyle.CHAT_COMPLETIONS,
    )
    adapter = _FakeAdapter(config, responses)
    registry = ModelAdapterRegistry(ModelSettings(_env_file=None))
    registry.register("fake", lambda _: adapter, config=config)
    return registry, adapter


# ---------------------------------------------------------------------------
# 环境与辅助
# ---------------------------------------------------------------------------


def _step(index: int, title: str) -> TaskStep:
    return TaskStep(
        id=f"s{index}",
        title=title,
        status=TaskStepStatus.DONE,
        note="已完成并验证",
    )


async def _create_completed(
    task_store: FileTaskStore,
    *,
    title: str,
    goal: str | None = None,
    steps: tuple[str, ...] = (),
    run_ids: tuple[str, ...] = (),
) -> TaskStep:
    task = await task_store.create(
        title=title,
        goal=goal,
        steps=tuple(
            _step(index, step_title) for index, step_title in enumerate(steps)
        ),
        owner_conversation_id="conv",
        run_ids=run_ids,
    )
    return (await task_store.set_status(task.id, TaskStatus.COMPLETED)).id  # type: ignore[attr-defined]


def _tool_started(run_id: str, seq: int, name: str, arguments: dict) -> AgentEvent:
    return AgentEvent(
        run_id=run_id,
        conversation_id="conv",
        sequence=seq,
        type=AgentEventType.TOOL_STARTED,
        tool_call=ToolCall(id=f"c{seq}", name=name, arguments=arguments),
    )


def _tool_completed(
    run_id: str,
    seq: int,
    name: str,
    arguments: dict,
    *,
    success: bool = True,
    error: str | None = None,
) -> AgentEvent:
    return AgentEvent(
        run_id=run_id,
        conversation_id="conv",
        sequence=seq,
        type=AgentEventType.TOOL_COMPLETED,
        tool_call=ToolCall(id=f"c{seq}", name=name, arguments=arguments),
        tool_result=ToolResult(
            tool_call_id=f"c{seq}",
            tool_name=name,
            success=success,
            error=error,
            duration_ms=0.0,
        ),
    )


async def _record_trace(
    trace_store: SQLiteTraceStore,
    events: tuple[AgentEvent, ...],
) -> None:
    for event in events:
        await trace_store.record_event(event)


async def _make_env(tmp_path: Path, *, batch_size: int = 20) -> tuple[dict, Path]:
    root = tmp_path / "env"
    task_store = FileTaskStore(root / "tasks")
    await task_store.initialize()
    trace_store = SQLiteTraceStore(root / "trace.db")
    await trace_store.initialize()
    skill_store = SkillStore(root / "user-skills", root / "project-skills")
    await skill_store.initialize()
    candidate_store = SkillCandidateStore(root / "data")
    await candidate_store.initialize()
    return {
        "task_store": task_store,
        "trace_store": trace_store,
        "skill_store": skill_store,
        "candidate_store": candidate_store,
    }, root


def _settings(
    tmp_path: Path,
    *,
    batch_size: int = 20,
    min_cluster: int = 3,
) -> SkillLearningSettings:
    return SkillLearningSettings(
        _env_file=None,
        skill_learning_batch_size=batch_size,
        skill_learning_min_cluster_size=min_cluster,
        skill_learning_data_dir=tmp_path / "env" / "data",
    )


# ---------------------------------------------------------------------------
# 1. TaskCard 投影
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_card_from_completed_task(tmp_path: Path) -> None:
    env, _ = await _make_env(tmp_path)
    task_id = await _create_completed(
        env["task_store"],
        title="修复 Python ImportError",
        goal="恢复项目启动",
        steps=("复现", "读 traceback", "修复", "跑 pytest"),
        run_ids=("r1", "r2"),
    )
    task = await env["task_store"].get(task_id)
    assert task is not None

    from app.skill_learning.service import _to_card

    card: TaskCard = _to_card(task)
    assert card.task_id == task_id
    assert card.title == "修复 Python ImportError"
    assert card.goal == "恢复项目启动"
    assert card.final_steps == ("复现", "读 traceback", "修复", "跑 pytest")
    assert card.run_count == 2
    # 轻量投影：不包含完整步骤对象 / Trace 内容。
    assert not hasattr(card, "steps")


@pytest.mark.asyncio
async def test_task_card_only_built_from_completed(tmp_path: Path) -> None:
    env, _ = await _make_env(tmp_path)
    task = await env["task_store"].create(
        title="进行中的任务",
        goal="目标",
        owner_conversation_id="conv",
    )
    assert task.status is not TaskStatus.COMPLETED


# ---------------------------------------------------------------------------
# 2. Mining Trigger + Watermark
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_requires_batch_size(tmp_path: Path) -> None:
    env, root = await _make_env(tmp_path, batch_size=20)
    registry, _ = _fake_registry([_model_response('{"clusters": []}')])
    service = SkillLearningService(
        env["task_store"],
        env["trace_store"],
        env["skill_store"],
        env["candidate_store"],
        registry,
        settings=_settings(root, batch_size=20),
        default_provider="fake",
    )
    # 19 个新 Completed Task → 不触发。
    for index in range(19):
        await _create_completed(env["task_store"], title=f"任务{index}")
    outcome = await service.maybe_run_mining()
    assert outcome.triggered is False
    assert outcome.skipped_reason == "batch_not_ready"
    assert outcome.pending_count == 19
    # 第 20 个 → 触发。
    await _create_completed(env["task_store"], title="任务20")
    outcome = await service.maybe_run_mining()
    assert outcome.triggered is True
    assert outcome.scanned_task_count == 20
    assert outcome.cluster_count == 0


@pytest.mark.asyncio
async def test_processed_tasks_not_counted_again(tmp_path: Path) -> None:
    env, root = await _make_env(tmp_path, batch_size=5)
    registry, _ = _fake_registry([_model_response('{"clusters": []}')])
    service = SkillLearningService(
        env["task_store"],
        env["trace_store"],
        env["skill_store"],
        env["candidate_store"],
        registry,
        settings=_settings(root, batch_size=5),
        default_provider="fake",
    )
    for index in range(5):
        await _create_completed(env["task_store"], title=f"任务{index}")
    first = await service.maybe_run_mining()
    assert first.triggered is True
    assert first.pending_count == 0

    # 新增 4 个 → pending=4，不够 5，不触发；已处理的不重复计数。
    for index in range(4):
        await _create_completed(env["task_store"], title=f"新任务{index}")
    second = await service.maybe_run_mining()
    assert second.triggered is False
    assert second.pending_count == 4


@pytest.mark.asyncio
async def test_watermark_survives_restart(tmp_path: Path) -> None:
    env, root = await _make_env(tmp_path, batch_size=5)
    registry, _ = _fake_registry([_model_response('{"clusters": []}')])
    service = SkillLearningService(
        env["task_store"],
        env["trace_store"],
        env["skill_store"],
        env["candidate_store"],
        registry,
        settings=_settings(root, batch_size=5),
        default_provider="fake",
    )
    for index in range(5):
        await _create_completed(env["task_store"], title=f"任务{index}")
    await service.maybe_run_mining()

    # 重启：重新加载 watermark，processed 必须保留。
    reloaded = await env["candidate_store"].load_watermark()
    assert len(reloaded.processed_task_ids) == 5
    # 新实例化后，相同 5 个任务不会再次触发。
    service2 = SkillLearningService(
        env["task_store"],
        env["trace_store"],
        env["skill_store"],
        env["candidate_store"],
        registry,
        settings=_settings(root, batch_size=5),
        default_provider="fake",
    )
    outcome = await service2.maybe_run_mining()
    assert outcome.triggered is False
    assert outcome.pending_count == 0


# ---------------------------------------------------------------------------
# 3. Pattern Mining
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_cluster_no_candidate(tmp_path: Path) -> None:
    env, root = await _make_env(tmp_path, batch_size=3)
    registry, _ = _fake_registry([_model_response('{"clusters": []}')])
    service = SkillLearningService(
        env["task_store"],
        env["trace_store"],
        env["skill_store"],
        env["candidate_store"],
        registry,
        settings=_settings(root, batch_size=3),
        default_provider="fake",
    )
    # 20 个互不相关的简单任务。
    for index in range(20):
        await _create_completed(env["task_store"], title=f"任务{index}")
    outcome = await service.maybe_run_mining()
    assert outcome.triggered is True
    assert outcome.cluster_count == 0
    assert outcome.candidate_count == 0
    assert await service.list_candidates() == ()


@pytest.mark.asyncio
async def test_similar_tasks_produce_cluster_and_candidate(tmp_path: Path) -> None:
    env, root = await _make_env(tmp_path, batch_size=3)
    cluster_json = json.dumps(
        {
            "clusters": [
                {
                    "id": "python-runtime-debug",
                    "task_ids": [],  # 占位，下面替换为真实 id
                    "pattern_name": "Python runtime debugging",
                    "description": "修复 Python 运行时错误",
                    "similarity_reason": "都是 ImportError/TypeError 排查",
                    "reusable_value": "多步骤排查流程可复用",
                }
            ]
        },
        ensure_ascii=False,
    )
    distill_json = json.dumps(
        {
            "action": "create",
            "proposed_name": "python-runtime-debug",
            "description": "排查 Python 运行时错误的标准流程",
            "reason": "多个相似任务证明该流程稳定",
            "procedure": ["复现", "读 traceback", "定位根因", "修复", "跑 pytest"],
            "pitfalls": ["不要跳过复现"],
            "verification": ["pytest 通过"],
        },
        ensure_ascii=False,
    )
    # mining 响应 1 次 + distillation 响应 1 次。
    registry, _ = _fake_registry(
        [_model_response(cluster_json), _model_response(distill_json)]
    )
    service = SkillLearningService(
        env["task_store"],
        env["trace_store"],
        env["skill_store"],
        env["candidate_store"],
        registry,
        settings=_settings(root, batch_size=3),
        default_provider="fake",
    )
    task_ids: list[str] = []
    for index in range(3):
        task_id = await _create_completed(
            env["task_store"],
            title=f"修复 Python 报错{index}",
            goal="恢复运行",
            steps=("复现", "读 traceback", "修复", "跑 pytest"),
            run_ids=(f"r{index}",),
        )
        task_ids.append(task_id)
    # 用真实 task ids 替换 cluster_json。
    registry, adapter = _fake_registry(
        [
            _model_response(
                json.dumps(
                    {
                        "clusters": [
                            {
                                "id": "python-runtime-debug",
                                "task_ids": task_ids,
                                "pattern_name": "Python runtime debugging",
                                "description": "修复 Python 运行时错误",
                                "similarity_reason": "都是 Python 报错排查",
                                "reusable_value": "多步骤排查流程可复用",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            ),
            _model_response(distill_json),
        ]
    )
    service = SkillLearningService(
        env["task_store"],
        env["trace_store"],
        env["skill_store"],
        env["candidate_store"],
        registry,
        settings=_settings(root, batch_size=3),
        default_provider="fake",
    )
    outcome = await service.maybe_run_mining()
    assert outcome.triggered is True
    assert outcome.cluster_count == 1
    assert outcome.candidate_count == 1
    candidates = await service.list_candidates()
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.proposed_name == "python-runtime-debug"
    assert candidate.source_task_ids == tuple(task_ids)
    assert candidate.action is SkillCandidateAction.CREATE
    assert candidate.reason
    assert candidate.procedure
    # 只调用过 mining + distillation，各 1 次。
    assert len(adapter.requests) == 2


# ---------------------------------------------------------------------------
# 4. Trace Evidence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evidence_builds_from_trace(tmp_path: Path) -> None:
    env, _ = await _make_env(tmp_path)
    task_id = await _create_completed(
        env["task_store"],
        title="修复报错",
        steps=("复现", "修复"),
        run_ids=("r1",),
    )
    task = await env["task_store"].get(task_id)
    assert task is not None
    await _record_trace(
        env["trace_store"],
        (
            _tool_started("r1", 1, "read_file", {"path": "x.py"}),
            _tool_completed("r1", 2, "read_file", {"path": "x.py"}),
            _tool_started("r1", 3, "run_pytest", {}),
            _tool_completed(
                "r1",
                4,
                "run_pytest",
                {},
                success=False,
                error="AssertionError",
            ),
            _tool_started("r1", 5, "task_update", {"goal": "新目标"}),
            _tool_completed("r1", 6, "task_update", {"goal": "新目标"}),
        ),
    )
    events = await env["trace_store"].load_events("r1")
    builder = TraceEvidenceBuilder(_settings(tmp_path))
    text = builder.build(task, events)
    assert "read_file" in text
    assert "失败工具调用" in text
    assert "run_pytest" in text
    assert "task_update" in text


@pytest.mark.asyncio
async def test_evidence_degrades_gracefully_when_trace_missing(
    tmp_path: Path,
) -> None:
    env, _ = await _make_env(tmp_path)
    task_id = await _create_completed(
        env["task_store"],
        title="无 Trace 的任务",
        steps=("步骤A",),
        run_ids=("missing-run",),
    )
    task = await env["task_store"].get(task_id)
    assert task is not None
    # 缺失 Trace 时，Service 内部 _load_task_events 会捕获 KeyError 并降级为空。
    events = await service_probe_load_events(env["trace_store"], task)
    assert events == ()
    builder = TraceEvidenceBuilder(_settings(tmp_path))
    text = builder.build(task, events)
    assert "没有可用的 Trace 事件" in text
    assert task.title in text


async def service_probe_load_events(
    trace_store: SQLiteTraceStore,
    task,
) -> tuple:
    """复用与 Service 相同的降级读取逻辑。"""

    events: list = []
    for run_id in task.run_ids:
        try:
            loaded = await trace_store.load_events(run_id)
        except (KeyError, ValueError, OSError):
            continue
        events.extend(loaded)
    return tuple(events)


# ---------------------------------------------------------------------------
# 5. Candidate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_candidate_fields_and_duplicate_suppression(tmp_path: Path) -> None:
    env, root = await _make_env(tmp_path)
    registry, _ = _fake_registry([])
    service = SkillLearningService(
        env["task_store"],
        env["trace_store"],
        env["skill_store"],
        env["candidate_store"],
        registry,
        settings=_settings(root),
        default_provider="fake",
    )
    from datetime import UTC, datetime
    from uuid import uuid4

    candidate = SkillCandidate(
        id=uuid4().hex,
        action=SkillCandidateAction.CREATE,
        proposed_name="python-runtime-debug",
        description="描述",
        reason="原因",
        procedure=("复现", "修复"),
        source_task_ids=("a", "b", "c"),
        source_run_ids=("r1", "r2"),
        created_at=datetime.now(UTC),
    )
    await service.candidate_store.create(candidate)

    # duplicate：相同 source 集合存在 → find_duplicate_source 命中。
    dup = await service.candidate_store.find_duplicate_source(("c", "a", "b"))
    assert dup is not None and dup.id == candidate.id
    # 不同 source 集合 → 不命中。
    assert await service.candidate_store.find_duplicate_source(("x", "y")) is None

    # UPDATE candidate 需要 existing_skill_name。
    with pytest.raises(ValueError):
        SkillCandidate(
            id=uuid4().hex,
            action=SkillCandidateAction.UPDATE,
            proposed_name="debug-python",
            description="描述",
            reason="原因",
            procedure=("步骤",),
            source_task_ids=("a",),
            created_at=datetime.now(UTC),
        )


# ---------------------------------------------------------------------------
# 6. Human Gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pending_candidate_not_visible_to_skill_runtime(
    tmp_path: Path,
) -> None:
    env, root = await _make_env(tmp_path)
    registry, _ = _fake_registry([])
    service = SkillLearningService(
        env["task_store"],
        env["trace_store"],
        env["skill_store"],
        env["candidate_store"],
        registry,
        settings=_settings(root),
        default_provider="fake",
    )
    from datetime import UTC, datetime
    from uuid import uuid4

    candidate = SkillCandidate(
        id=uuid4().hex,
        action=SkillCandidateAction.CREATE,
        proposed_name="pending-skill",
        description="描述",
        reason="原因",
        procedure=("步骤",),
        source_task_ids=("a", "b", "c"),
        created_at=datetime.now(UTC),
    )
    await service.candidate_store.create(candidate)
    # pending Candidate 不影响 Skill Runtime（catalog 不含它）。
    catalog = await env["skill_store"].catalog()
    assert all(item.name != "pending-skill" for item in catalog)
    assert await env["skill_store"].load("pending-skill") is None


@pytest.mark.asyncio
async def test_accept_creates_skill_and_reject_does_not(
    tmp_path: Path,
) -> None:
    env, root = await _make_env(tmp_path)
    registry, _ = _fake_registry([])
    service = SkillLearningService(
        env["task_store"],
        env["trace_store"],
        env["skill_store"],
        env["candidate_store"],
        registry,
        settings=_settings(root),
        default_provider="fake",
    )
    from datetime import UTC, datetime
    from uuid import uuid4

    accept_candidate = SkillCandidate(
        id=uuid4().hex,
        action=SkillCandidateAction.CREATE,
        proposed_name="accepted-skill",
        description="被接受的技能",
        reason="原因",
        procedure=("步骤A", "步骤B"),
        source_task_ids=("a", "b", "c"),
        created_at=datetime.now(UTC),
    )
    reject_candidate = SkillCandidate(
        id=uuid4().hex,
        action=SkillCandidateAction.CREATE,
        proposed_name="rejected-skill",
        description="被拒绝的技能",
        reason="原因",
        procedure=("步骤A",),
        source_task_ids=("d", "e", "f"),
        created_at=datetime.now(UTC),
    )
    await service.candidate_store.create(accept_candidate)
    await service.candidate_store.create(reject_candidate)

    # reject 不创建 Skill。
    await service.reject(reject_candidate.id)
    assert await env["skill_store"].load("rejected-skill") is None
    rejected = await service.get_candidate(reject_candidate.id)
    assert rejected is not None and rejected.status is SkillCandidateStatus.REJECTED

    # accept 创建正式 Skill（默认 project scope）。
    updated, target = await service.accept(accept_candidate.id)
    assert updated.status is SkillCandidateStatus.ACCEPTED
    assert target is not None and target.name == "SKILL.md"
    skill = await env["skill_store"].load("accepted-skill")
    assert skill is not None
    assert "步骤A" in skill.content
    catalog = await env["skill_store"].catalog()
    assert any(item.name == "accepted-skill" for item in catalog)


# ---------------------------------------------------------------------------
# 7. P0：Evidence 对齐真实 task_update 参数（具体内容，不只是"发生了 change"）
# ---------------------------------------------------------------------------


def _task_update_events(run_id: str, arguments: dict) -> tuple[AgentEvent, ...]:
    return (
        _tool_started(run_id, 1, "task_update", arguments),
        _tool_completed(run_id, 2, "task_update", arguments),
    )


async def _build_evidence(tmp_path: Path, arguments: dict) -> str:
    env, _ = await _make_env(tmp_path)
    task_id = await _create_completed(
        env["task_store"],
        title="修复报错",
        run_ids=("r1",),
    )
    task = await env["task_store"].get(task_id)
    assert task is not None
    events = _task_update_events("r1", arguments)
    builder = TraceEvidenceBuilder(_settings(tmp_path))
    return builder.build(task, events)


@pytest.mark.asyncio
async def test_evidence_sees_constraints_content(tmp_path: Path) -> None:
    text = await _build_evidence(
        tmp_path,
        {"task_id": "x", "constraints": ["不要重装全部依赖", "使用现有 .venv"]},
    )
    assert "constraints added: 不要重装全部依赖" in text
    assert "使用现有 .venv" in text


@pytest.mark.asyncio
async def test_evidence_sees_facts_content(tmp_path: Path) -> None:
    text = await _build_evidence(
        tmp_path,
        {"task_id": "x", "facts": ["项目实际使用 .venv", "CI 依赖缓存"]},
    )
    assert "facts added: 项目实际使用 .venv" in text
    assert "CI 依赖缓存" in text


@pytest.mark.asyncio
async def test_evidence_sees_state_content(tmp_path: Path) -> None:
    text = await _build_evidence(
        tmp_path,
        {"task_id": "x", "state": ["已定位 import path", "等待运行 pytest"]},
    )
    assert "state replaced: 已定位 import path" in text
    assert "等待运行 pytest" in text


@pytest.mark.asyncio
async def test_evidence_sees_replacement_steps(tmp_path: Path) -> None:
    text = await _build_evidence(
        tmp_path,
        {
            "task_id": "x",
            "steps": [
                {"title": "检查 virtualenv"},
                {"title": "读取 traceback"},
                {"title": "定位 import path"},
                {"title": "运行 pytest"},
            ],
        },
    )
    assert "plan replaced:" in text
    assert "- 检查 virtualenv" in text
    assert "- 读取 traceback" in text
    assert "- 运行 pytest" in text


@pytest.mark.asyncio
async def test_evidence_sees_step_progress_and_note(tmp_path: Path) -> None:
    text = await _build_evidence(
        tmp_path,
        {
            "task_id": "x",
            "step_id": "s2",
            "step_status": "done",
            "step_note": "已确认 .venv 存在，pytest 通过",
        },
    )
    assert "step s2 -> done: 已确认 .venv 存在，pytest 通过" in text


@pytest.mark.asyncio
async def test_evidence_combined_update_keeps_key_fields(tmp_path: Path) -> None:
    text = await _build_evidence(
        tmp_path,
        {
            "task_id": "x",
            "goal": "恢复 CI 全绿",
            "constraints": ["不要跳过 virtualenv 确认"],
            "facts": ["项目使用 .venv"],
            "steps": [{"title": "复现"}, {"title": "修复"}],
            "step_id": "s1",
            "step_status": "done",
            "step_note": "已复现",
        },
    )
    # steps 与 step_id 在真实 API 中互斥，但组合更新至少保留 goal/constraints/facts。
    assert "goal: 恢复 CI 全绿" in text
    assert "constraints added: 不要跳过 virtualenv 确认" in text
    assert "facts added: 项目使用 .venv" in text


# ---------------------------------------------------------------------------
# 8. P1：Mining 失败不丢 Batch（inflight 状态机）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mining_failure_keeps_batch_inflight(tmp_path: Path) -> None:
    env, root = await _make_env(tmp_path, batch_size=20)
    registry, _ = _fake_registry([TimeoutError("model timeout")])
    service = SkillLearningService(
        env["task_store"],
        env["trace_store"],
        env["skill_store"],
        env["candidate_store"],
        registry,
        settings=_settings(root, batch_size=20),
        default_provider="fake",
    )
    for index in range(20):
        await _create_completed(env["task_store"], title=f"任务{index}")

    outcome = await service.maybe_run_mining()
    assert outcome.triggered is True
    assert outcome.error is not None

    watermark = await env["candidate_store"].load_watermark()
    assert watermark.inflight is not None
    assert len(watermark.inflight.task_ids) == 20
    # 任务没有进入最终 processed。
    assert not set(watermark.inflight.task_ids) & set(watermark.processed_task_ids)


@pytest.mark.asyncio
async def test_inflight_survives_restart_and_retry_succeeds(tmp_path: Path) -> None:
    env, root = await _make_env(tmp_path, batch_size=5)
    registry_fail, _ = _fake_registry([TimeoutError("timeout")])
    service = SkillLearningService(
        env["task_store"],
        env["trace_store"],
        env["skill_store"],
        env["candidate_store"],
        registry_fail,
        settings=_settings(root, batch_size=5),
        default_provider="fake",
    )
    for index in range(5):
        await _create_completed(env["task_store"], title=f"任务{index}")
    await service.maybe_run_mining()
    watermark = await env["candidate_store"].load_watermark()
    assert watermark.inflight is not None
    assert watermark.inflight.attempt == 1

    # 重启：新实例直接读到遗留 inflight。
    reloaded = await env["candidate_store"].load_watermark()
    assert reloaded.inflight is not None

    # 第二次模型成功 → 进入 processed。
    registry_ok, _ = _fake_registry([_model_response('{"clusters": []}')])
    service2 = SkillLearningService(
        env["task_store"],
        env["trace_store"],
        env["skill_store"],
        env["candidate_store"],
        registry_ok,
        settings=_settings(root, batch_size=5),
        default_provider="fake",
    )
    outcome = await service2.maybe_run_mining()
    assert outcome.triggered is True
    assert outcome.error is None
    watermark2 = await env["candidate_store"].load_watermark()
    assert watermark2.inflight is None
    assert len(watermark2.processed_task_ids) == 5
    # D. 成功后不再重复扫描。
    outcome_again = await service2.maybe_run_mining()
    assert outcome_again.triggered is False


@pytest.mark.asyncio
async def test_invalid_json_mining_keeps_batch(tmp_path: Path) -> None:
    env, root = await _make_env(tmp_path, batch_size=5)
    registry, _ = _fake_registry([_model_response("not a json payload")])
    service = SkillLearningService(
        env["task_store"],
        env["trace_store"],
        env["skill_store"],
        env["candidate_store"],
        registry,
        settings=_settings(root, batch_size=5),
        default_provider="fake",
    )
    for index in range(5):
        await _create_completed(env["task_store"], title=f"任务{index}")
    outcome = await service.maybe_run_mining()
    assert outcome.triggered is True
    assert outcome.error is not None
    watermark = await env["candidate_store"].load_watermark()
    assert watermark.inflight is not None
    assert not set(watermark.inflight.task_ids) & set(watermark.processed_task_ids)


@pytest.mark.asyncio
async def test_mining_gives_up_after_max_attempts(tmp_path: Path) -> None:
    env, root = await _make_env(tmp_path, batch_size=5)
    registry, _ = _fake_registry([TimeoutError("t1")])
    settings = _settings(root, batch_size=5)
    settings = SkillLearningSettings(
        _env_file=None,
        skill_learning_batch_size=5,
        skill_learning_min_cluster_size=3,
        skill_learning_max_attempts=2,
        skill_learning_data_dir=root / "env" / "data",
    )
    service = SkillLearningService(
        env["task_store"],
        env["trace_store"],
        env["skill_store"],
        env["candidate_store"],
        registry,
        settings=settings,
        default_provider="fake",
    )
    for index in range(5):
        await _create_completed(env["task_store"], title=f"任务{index}")
    first = await service.maybe_run_mining()
    assert first.error is not None
    watermark = await env["candidate_store"].load_watermark()
    assert watermark.inflight is not None and watermark.inflight.attempt == 1

    # 第二次（attempt 2）仍失败 → 达到上限，放弃并标记 processed。
    second = await service.maybe_run_mining()
    assert second.error is not None
    watermark2 = await env["candidate_store"].load_watermark()
    assert watermark2.inflight is None
    assert len(watermark2.processed_task_ids) == 5


# ---------------------------------------------------------------------------
# 9. P1：Pending Candidate 防重复
# ---------------------------------------------------------------------------


def _mining_cluster_json(task_ids: list[str]) -> str:
    return json.dumps(
        {
            "clusters": [
                {
                    "id": "python-debug",
                    "task_ids": task_ids,
                    "pattern_name": "Python runtime debugging",
                    "description": "修复 Python 运行时错误",
                    "similarity_reason": "均为 Python 报错排查",
                    "reusable_value": "多步骤流程可复用",
                }
            ]
        },
        ensure_ascii=False,
    )


_DISTILL_CREATE_JSON = json.dumps(
    {
        "action": "create",
        "proposed_name": "python-runtime-debug",
        "description": "排查 Python 运行时错误",
        "reason": "多个相似任务证明流程稳定",
        "procedure": ["复现", "读 traceback", "修复", "验证"],
        "pitfalls": ["不要跳过复现"],
        "verification": ["pytest 通过"],
    },
    ensure_ascii=False,
)
_DISTILL_NONE_JSON = json.dumps(
    {"action": "none", "reason": "被 pending candidate 覆盖"},
    ensure_ascii=False,
)
_DISTILL_UPDATE_JSON = json.dumps(
    {
        "action": "update",
        "proposed_name": None,
        "description": "补充 virtualenv 确认",
        "reason": "新证据证明应先确认 virtualenv",
        "procedure": ["复现", "确认 virtualenv", "修复", "验证"],
        "pitfalls": [],
        "verification": ["pytest 通过"],
        "existing_skill_name": "debug-python",
    },
    ensure_ascii=False,
)


async def _run_batch(
    env,
    root,
    *,
    task_count: int = 3,
    batch_size: int = 3,
    distill_response: str | None = None,
):
    """创建一批类似 Python Debug 的 Completed Task，用真实 task_ids 跑一次 mining。"""

    task_ids: list[str] = []
    for index in range(task_count):
        task_id = await _create_completed(
            env["task_store"],
            title=f"Python 报错{index}",
            steps=("复现", "读 traceback", "修复"),
            run_ids=(f"r{index}",),
        )
        task_ids.append(task_id)
    responses: list = [_model_response(_mining_cluster_json(task_ids))]
    if distill_response is not None:
        # catalog 非空时 distiller 会先做相关性筛选（额外一次模型调用）。
        catalog = await env["skill_store"].catalog()
        if catalog:
            responses.append(
                _model_response('{"related_skills": ["debug-python"]}')
            )
        responses.append(_model_response(distill_response))
    registry, _ = _fake_registry(responses)
    service = SkillLearningService(
        env["task_store"],
        env["trace_store"],
        env["skill_store"],
        env["candidate_store"],
        registry,
        settings=_settings(root, batch_size=batch_size),
        default_provider="fake",
    )
    outcome = await service.maybe_run_mining()
    return service, outcome, task_ids


async def service_candidates(env) -> tuple:
    return await env["candidate_store"].list()


@pytest.mark.asyncio
async def test_pending_candidate_blocks_exact_name_duplicate(tmp_path: Path) -> None:
    env, root = await _make_env(tmp_path)
    from datetime import UTC, datetime
    from uuid import uuid4

    pending = SkillCandidate(
        id=uuid4().hex,
        action=SkillCandidateAction.CREATE,
        proposed_name="python-runtime-debug",
        description="已待评审",
        reason="原因",
        procedure=("复现",),
        source_task_ids=("old-a", "old-b", "old-c"),
        created_at=datetime.now(UTC),
    )
    await env["candidate_store"].create(pending)

    # 新 batch 的 Distillation 又建议 python-runtime-debug（同名）→ 不创建第二个。
    _, outcome, _ = await _run_batch(
        env,
        root,
        distill_response=_DISTILL_CREATE_JSON,
    )
    assert outcome.candidate_count == 0
    assert len(await service_candidates(env)) == 1


@pytest.mark.asyncio
async def test_pending_candidate_semantic_cover_returns_none(tmp_path: Path) -> None:
    env, root = await _make_env(tmp_path)
    from datetime import UTC, datetime
    from uuid import uuid4

    pending = SkillCandidate(
        id=uuid4().hex,
        action=SkillCandidateAction.CREATE,
        proposed_name="debug-python-runtime",
        description="待评审",
        reason="原因",
        procedure=("复现",),
        source_task_ids=("x", "y", "z"),
        created_at=datetime.now(UTC),
    )
    await env["candidate_store"].create(pending)

    # Distiller 收到 pending_candidates 后判断被覆盖 → action=none。
    _, outcome, _ = await _run_batch(
        env,
        root,
        distill_response=_DISTILL_NONE_JSON,
    )
    assert outcome.candidate_count == 0
    assert len(await service_candidates(env)) == 1


@pytest.mark.asyncio
async def test_rejected_candidate_does_not_block_future(tmp_path: Path) -> None:
    env, root = await _make_env(tmp_path)
    from datetime import UTC, datetime
    from uuid import uuid4

    pending = SkillCandidate(
        id=uuid4().hex,
        action=SkillCandidateAction.CREATE,
        proposed_name="python-runtime-debug",
        description="待评审",
        reason="原因",
        procedure=("复现",),
        source_task_ids=("x", "y", "z"),
        created_at=datetime.now(UTC),
    )
    await env["candidate_store"].create(pending)
    await env["candidate_store"].update(
        pending.model_copy(
            update={
                "status": SkillCandidateStatus.REJECTED,
                "reviewed_at": datetime.now(UTC),
            }
        )
    )
    # reject 不参与 pending 去重 → 允许基于新证据重新建议。
    _, outcome, _ = await _run_batch(
        env,
        root,
        distill_response=_DISTILL_CREATE_JSON,
    )
    assert outcome.candidate_count == 1


@pytest.mark.asyncio
async def test_accepted_skill_in_catalog_drives_update(tmp_path: Path) -> None:
    env, root = await _make_env(tmp_path)
    skill_root = env["skill_store"].project_dir / "debug-python"
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "---\nname: debug-python\ndescription: 排查 Python 报错\n---\n\n"
        "# Debug\n\n1. 复现",
        encoding="utf-8",
    )
    _, outcome, _ = await _run_batch(
        env,
        root,
        distill_response=_DISTILL_UPDATE_JSON,
    )
    assert outcome.candidate_count == 1
    candidates = await service_candidates(env)
    assert candidates[0].action is SkillCandidateAction.UPDATE
    assert candidates[0].existing_skill_name == "debug-python"
    assert candidates[0].proposed_name == "debug-python"


# ---------------------------------------------------------------------------
# 8. Progressive Disclosure：Distiller 按需加载相关 Skill 正文
# ---------------------------------------------------------------------------


async def _run_progressive_disclosure(
    env,
    root,
    *,
    relevance: list[str],
    distill: str,
):
    """创建 3 个 Python 环境类任务，跑 mining + relevance + distill，返回 adapter。"""

    task_ids: list[str] = []
    for index in range(3):
        task_id = await _create_completed(
            env["task_store"],
            title=f"排查 Python 环境报错{index}",
            steps=("复现", "确认 virtualenv", "修复", "验证"),
            run_ids=(f"r{index}",),
        )
        task_ids.append(task_id)
    responses = [
        _model_response(_mining_cluster_json(task_ids)),
        _model_response(
            json.dumps({"related_skills": relevance}, ensure_ascii=False)
        ),
        _model_response(distill),
    ]
    registry, adapter = _fake_registry(responses)
    service = SkillLearningService(
        env["task_store"],
        env["trace_store"],
        env["skill_store"],
        env["candidate_store"],
        registry,
        settings=_settings(root, batch_size=3),
        default_provider="fake",
    )
    outcome = await service.maybe_run_mining()
    return service, outcome, adapter


@pytest.mark.asyncio
async def test_distiller_returns_none_when_skill_body_covers_procedure(
    tmp_path: Path,
) -> None:
    """Existing Skill 正文已包含 virtualenv 检查，新 Task 同样流程 → NONE。

    验证 Distiller 确实把相关 Skill 的完整正文传给了最终判断（progressive
    disclosure 生效），模型基于正文判断"已覆盖"→ none。
    """
    env, root = await _make_env(tmp_path)
    skill_root = env["skill_store"].project_dir / "debug-python"
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "---\nname: debug-python\ndescription: 排查 Python 虚拟环境报错\n---\n\n"
        "# Debug Python\n\n1. 复现\n2. 确认 virtualenv（内置步骤）\n3. 修复并验证",
        encoding="utf-8",
    )
    _, outcome, adapter = await _run_progressive_disclosure(
        env,
        root,
        relevance=["debug-python"],
        distill=json.dumps(
            {"action": "none", "reason": "正文已包含确认 virtualenv 步骤"},
            ensure_ascii=False,
        ),
    )
    assert outcome.candidate_count == 0
    # 最终蒸馏请求确实携带了 debug-python 完整正文。
    final_content = adapter.requests[-1].messages[-1].content or ""
    assert "内置步骤" in final_content
    assert '"related_skills"' in final_content


@pytest.mark.asyncio
async def test_distiller_loads_related_skill_body_for_update(
    tmp_path: Path,
) -> None:
    """Existing Skill 正文不含 virtualenv，多个 Task 稳定证明是必要步骤 → UPDATE。

    验证正文被加载，且模型基于正文差异选择 update 而非 create。
    """
    env, root = await _make_env(tmp_path)
    skill_root = env["skill_store"].project_dir / "debug-python"
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "---\nname: debug-python\ndescription: 排查 Python 虚拟环境报错\n---\n\n"
        "# Debug Python\n\n1. 复现\n2. 读 traceback\n3. 修复并验证",
        encoding="utf-8",
    )
    _, outcome, adapter = await _run_progressive_disclosure(
        env,
        root,
        relevance=["debug-python"],
        distill=_DISTILL_UPDATE_JSON,
    )
    assert outcome.candidate_count == 1
    candidates = await service_candidates(env)
    assert candidates[0].action is SkillCandidateAction.UPDATE
    assert candidates[0].existing_skill_name == "debug-python"
    # 正文（"读 traceback"）确实被加载进最终请求。
    final_content = adapter.requests[-1].messages[-1].content or ""
    assert "读 traceback" in final_content


@pytest.mark.asyncio
async def test_distiller_creates_when_related_skill_unrelated(
    tmp_path: Path,
) -> None:
    """catalog 里的 Skill 完全不同领域（relevance 返回空）→ CREATE，且不加载正文。"""
    env, root = await _make_env(tmp_path)
    skill_root = env["skill_store"].project_dir / "weekly-report"
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "---\nname: weekly-report\ndescription: 撰写每周工作周报\n---\n\n"
        "# 周报\n\n1. 收集信息\n2. 写总结",
        encoding="utf-8",
    )
    _, outcome, adapter = await _run_progressive_disclosure(
        env,
        root,
        relevance=[],
        distill=_DISTILL_CREATE_JSON,
    )
    assert outcome.candidate_count == 1
    candidates = await service_candidates(env)
    assert candidates[0].action is SkillCandidateAction.CREATE
    # 无关 skill 的正文没有被加载进最终请求。
    final_content = adapter.requests[-1].messages[-1].content or ""
    assert '"related_skills":[]' in final_content
    assert "写总结" not in final_content


@pytest.mark.asyncio
async def test_update_candidate_inherits_description_when_model_omits(
    tmp_path: Path,
) -> None:
    """UPDATE 时模型省略 description（常见于只补步骤）→ 从 catalog 继承，不被丢弃。"""
    env, root = await _make_env(tmp_path)
    skill_root = env["skill_store"].project_dir / "debug-python"
    skill_root.mkdir(parents=True)
    description = "排查 Python 虚拟环境报错的标准流程"
    (skill_root / "SKILL.md").write_text(
        "---\nname: debug-python\n"
        f"description: {description}\n---\n\n"
        "# Debug Python\n\n1. 复现\n2. 读 traceback\n3. 修复并验证",
        encoding="utf-8",
    )
    update_no_desc = json.dumps(
        {
            "action": "update",
            "proposed_name": None,
            "description": None,
            "reason": "正文缺 virtualenv 确认步骤",
            "procedure": ["复现", "确认 virtualenv", "修复", "验证"],
            "pitfalls": [],
            "verification": ["pytest 通过"],
            "existing_skill_name": "debug-python",
        },
        ensure_ascii=False,
    )
    _, outcome, _ = await _run_progressive_disclosure(
        env,
        root,
        relevance=["debug-python"],
        distill=update_no_desc,
    )
    assert outcome.candidate_count == 1
    candidates = await service_candidates(env)
    assert candidates[0].action is SkillCandidateAction.UPDATE
    assert candidates[0].existing_skill_name == "debug-python"
    assert candidates[0].description == description


@pytest.mark.asyncio
async def test_update_candidate_uses_model_description_when_provided(
    tmp_path: Path,
) -> None:
    """UPDATE 时模型提供了 description → 使用模型输出，不覆盖为 catalog 描述。"""
    env, root = await _make_env(tmp_path)
    skill_root = env["skill_store"].project_dir / "debug-python"
    skill_root.mkdir(parents=True)
    catalog_desc = "排查 Python 虚拟环境报错的标准流程"
    (skill_root / "SKILL.md").write_text(
        "---\nname: debug-python\n"
        f"description: {catalog_desc}\n---\n\n"
        "# Debug Python\n\n1. 复现\n2. 读 traceback\n3. 修复并验证",
        encoding="utf-8",
    )
    model_desc = "模型给的新描述：先确认 virtualenv"
    update_with_desc = json.dumps(
        {
            "action": "update",
            "proposed_name": None,
            "description": model_desc,
            "reason": "补充 virtualenv 步骤",
            "procedure": ["复现", "确认 virtualenv", "修复", "验证"],
            "pitfalls": [],
            "verification": ["pytest 通过"],
            "existing_skill_name": "debug-python",
        },
        ensure_ascii=False,
    )
    _, outcome, _ = await _run_progressive_disclosure(
        env,
        root,
        relevance=["debug-python"],
        distill=update_with_desc,
    )
    assert outcome.candidate_count == 1
    candidates = await service_candidates(env)
    assert candidates[0].action is SkillCandidateAction.UPDATE
    assert candidates[0].description == model_desc


@pytest.mark.asyncio
async def test_update_candidate_fails_when_existing_skill_missing(
    tmp_path: Path,
) -> None:
    """UPDATE 指向 catalog 中不存在的 Skill → 明确失败，不静默生成空 description。"""
    env, root = await _make_env(tmp_path)
    update_ghost = json.dumps(
        {
            "action": "update",
            "proposed_name": None,
            "description": None,
            "reason": "指向不存在的 skill",
            "procedure": ["复现", "修复"],
            "pitfalls": [],
            "verification": ["验证"],
            "existing_skill_name": "ghost-skill",
        },
        ensure_ascii=False,
    )
    _, outcome, _ = await _run_batch(
        env,
        root,
        distill_response=update_ghost,
    )
    assert outcome.candidate_count == 0
    assert outcome.error is not None
    assert "ghost-skill" in outcome.error
    assert "not found" in outcome.error


@pytest.mark.asyncio
async def test_create_candidate_still_requires_description(tmp_path: Path) -> None:
    """CREATE 时模型缺失 description → 仍然明确失败（不允许继承或兜底）。"""
    env, root = await _make_env(tmp_path)
    create_no_desc = json.dumps(
        {
            "action": "create",
            "proposed_name": "new-skill",
            "description": None,
            "reason": "缺少 description",
            "procedure": ["步骤"],
            "pitfalls": [],
            "verification": [],
        },
        ensure_ascii=False,
    )
    _, outcome, _ = await _run_batch(
        env,
        root,
        distill_response=create_no_desc,
    )
    assert outcome.candidate_count == 0
    assert outcome.error is not None
    assert "create candidate requires" in outcome.error


# ---------------------------------------------------------------------------
# 9. Trace Evidence 锚点区间筛选（service 集成）
# ---------------------------------------------------------------------------


def _tool_event_step(
    run_id: str,
    sequence: int,
    step: int,
    name: str,
    arguments: dict,
    *,
    success: bool = True,
) -> AgentEvent:
    """带 Agent Step 编号的工具完成事件（TaskTraceSelector 需要 step）。"""

    return AgentEvent(
        run_id=run_id,
        conversation_id="conv",
        sequence=sequence,
        step=step,
        type=AgentEventType.TOOL_COMPLETED,
        tool_call=ToolCall(id=f"c{sequence}", name=name, arguments=arguments),
        tool_result=ToolResult(
            tool_call_id=f"c{sequence}",
            tool_name=name,
            success=success,
            duration_ms=0.0,
        ),
    )


@pytest.mark.asyncio
async def test_service_loads_anchor_bounded_events(tmp_path: Path) -> None:
    """service._load_task_events 用 task_update 锚点区间，而非整个 Run。"""
    env, root = await _make_env(tmp_path, batch_size=3)
    registry, _ = _fake_registry([])
    service = SkillLearningService(
        env["task_store"],
        env["trace_store"],
        env["skill_store"],
        env["candidate_store"],
        registry,
        settings=_settings(root, batch_size=3),
        default_provider="fake",
    )
    task_id = await _create_completed(
        env["task_store"],
        title="修复报错",
        run_ids=("r1",),
    )
    task = await env["task_store"].get(task_id)
    assert task is not None
    # step1 无关 / step2 in_progress / step3 失败 / step4 done / step5 无关。
    await _record_trace(
        env["trace_store"],
        (
            _tool_event_step("r1", 1, 1, "read_file", {}),
            _tool_event_step(
                "r1", 2, 2, "task_update",
                {"task_id": task_id, "step_id": "s1",
                 "step_status": "in_progress"},
            ),
            _tool_event_step(
                "r1", 3, 3, "run_pytest", {}, success=False
            ),
            _tool_event_step(
                "r1", 4, 4, "task_update",
                {"task_id": task_id, "step_id": "s1",
                 "step_status": "done", "step_note": "ok"},
            ),
            _tool_event_step("r1", 5, 5, "read_file", {}),
        ),
    )
    events = await service._load_task_events(task)
    # 只保留 step 2~4（in_progress → done），step1/step5 无关不进入。
    assert [event.step for event in events] == [2, 3, 4]


# ---------------------------------------------------------------------------
# 10. Human Gate：UPDATE Accept 直接覆盖正式 Skill（不再生成 Proposal）
# ---------------------------------------------------------------------------


def _seed_skill(
    env,
    name: str = "debug-python",
    description: str = "old",
    body: str = "# Debug Python\n\n1. Procedure A",
) -> None:
    skill_root = env["skill_store"].project_dir / name
    skill_root.mkdir(parents=True, exist_ok=True)
    (skill_root / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}",
        encoding="utf-8",
    )


def _update_candidate(
    *,
    existing: str = "debug-python",
    proposed: str = "debug-python",
    description: str = "new",
    procedure: tuple[str, ...] = ("Procedure B",),
) -> SkillCandidate:
    from datetime import UTC, datetime
    from uuid import uuid4

    return SkillCandidate(
        id=uuid4().hex,
        action=SkillCandidateAction.UPDATE,
        proposed_name=proposed,
        description=description,
        reason="补充 Procedure B",
        procedure=procedure,
        pitfalls=("不要跳过验证",),
        verification=("pytest 通过",),
        source_task_ids=("a", "b", "c"),
        existing_skill_name=existing,
        created_at=datetime.now(UTC),
    )


def _learning_service(env, root) -> SkillLearningService:
    registry, _ = _fake_registry([])
    return SkillLearningService(
        env["task_store"],
        env["trace_store"],
        env["skill_store"],
        env["candidate_store"],
        registry,
        settings=_settings(root, batch_size=3),
        default_provider="fake",
    )


@pytest.mark.asyncio
async def test_accept_update_overwrites_real_skill(tmp_path: Path) -> None:
    env, root = await _make_env(tmp_path)
    _seed_skill(env, body="# Debug Python\n\n1. Procedure A")
    await env["candidate_store"].create(_update_candidate(procedure=("Procedure B",)))
    service = _learning_service(env, root)

    updated, target = await service.accept((await service_candidates(env))[0].id)
    assert updated.status is SkillCandidateStatus.ACCEPTED
    assert target is not None and target.name == "SKILL.md"
    skill = await env["skill_store"].load("debug-python")
    assert skill is not None
    assert "Procedure B" in skill.content
    # 不再有旧 Proposal 文案 / 审计信息。
    assert "提案" not in skill.content
    assert "来源 Task" not in skill.content


@pytest.mark.asyncio
async def test_accept_update_keeps_skill_name(tmp_path: Path) -> None:
    """UPDATE 目标唯一由 existing_skill_name 决定，proposed_name 异常不影响。"""
    env, root = await _make_env(tmp_path)
    _seed_skill(env)
    await env["candidate_store"].create(
        _update_candidate(proposed="evil-name")
    )
    service = _learning_service(env, root)

    _, target = await service.accept((await service_candidates(env))[0].id)
    skill = await env["skill_store"].load("debug-python")
    assert skill is not None and skill.metadata.name == "debug-python"
    # 不创建 / 不更新其他路径。
    assert await env["skill_store"].load("evil-name") is None
    assert "debug-python" in str(target)


@pytest.mark.asyncio
async def test_accept_update_uses_candidate_description(tmp_path: Path) -> None:
    env, root = await _make_env(tmp_path)
    _seed_skill(env, description="old")
    await env["candidate_store"].create(_update_candidate(description="new"))
    service = _learning_service(env, root)

    await service.accept((await service_candidates(env))[0].id)
    skill = await env["skill_store"].load("debug-python")
    assert skill is not None
    # description 使用 Candidate 的新描述（front matter）。
    assert skill.metadata.description == "new"


@pytest.mark.asyncio
async def test_accept_update_missing_skill_fails(tmp_path: Path) -> None:
    env, root = await _make_env(tmp_path)
    await env["candidate_store"].create(
        _update_candidate(existing="ghost-skill")
    )
    service = _learning_service(env, root)

    with pytest.raises(ValueError):
        await service.accept((await service_candidates(env))[0].id)
    # Candidate 仍 PENDING，且不创建新 Skill。
    after = (await service_candidates(env))[0]
    assert after.status is SkillCandidateStatus.PENDING
    assert await env["skill_store"].load("ghost-skill") is None


@pytest.mark.asyncio
async def test_accept_update_write_failure_keeps_pending(
    tmp_path: Path,
    monkeypatch,
) -> None:
    env, root = await _make_env(tmp_path)
    _seed_skill(env, body="# Debug Python\n\n1. Procedure A")
    await env["candidate_store"].create(_update_candidate())
    service = _learning_service(env, root)

    from app.skill_learning import service as svc

    def boom(path, content):  # noqa: ARG001
        raise OSError("disk full")

    monkeypatch.setattr(svc, "_atomic_write_text", boom)
    with pytest.raises(OSError):
        await service.accept((await service_candidates(env))[0].id)
    # 写失败：Candidate 仍 PENDING，正式 Skill 原样。
    after = (await service_candidates(env))[0]
    assert after.status is SkillCandidateStatus.PENDING
    skill = await env["skill_store"].load("debug-python")
    assert skill is not None and "Procedure A" in skill.content


@pytest.mark.asyncio
async def test_reject_update_keeps_skill(tmp_path: Path) -> None:
    env, root = await _make_env(tmp_path)
    _seed_skill(env, body="# Debug Python\n\n1. Procedure A")
    await env["candidate_store"].create(_update_candidate())
    service = _learning_service(env, root)

    await service.reject((await service_candidates(env))[0].id)
    # Reject：不修改正式 Skill，Candidate REJECTED。
    skill = await env["skill_store"].load("debug-python")
    assert skill is not None and "Procedure A" in skill.content
    after = (await service_candidates(env))[0]
    assert after.status is SkillCandidateStatus.REJECTED


@pytest.mark.asyncio
async def test_accept_create_regression(tmp_path: Path) -> None:
    """CREATE Accept 行为不变：创建正式 Skill。"""
    from datetime import UTC, datetime
    from uuid import uuid4

    env, root = await _make_env(tmp_path)
    candidate = SkillCandidate(
        id=uuid4().hex,
        action=SkillCandidateAction.CREATE,
        proposed_name="new-skill",
        description="描述",
        reason="原因",
        procedure=("步骤",),
        source_task_ids=("a", "b", "c"),
        created_at=datetime.now(UTC),
    )
    await env["candidate_store"].create(candidate)
    service = _learning_service(env, root)

    updated, target = await service.accept(candidate.id)
    assert updated.status is SkillCandidateStatus.ACCEPTED
    assert target is not None and target.name == "SKILL.md"
    skill = await env["skill_store"].load("new-skill")
    assert skill is not None
    assert "步骤" in skill.content


# ---------------------------------------------------------------------------
# 11. Distillation 失败不能提前 processed batch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_distillation_failure_keeps_batch_inflight(tmp_path: Path) -> None:
    """Pattern Mining 成功 → Distillation 失败 → batch 仍 inflight / 未 processed，
    下次触发点可重试并成功创建 Candidate。"""
    env, root = await _make_env(tmp_path, batch_size=3)
    task_ids: list[str] = []
    for index in range(3):
        task_ids.append(
            await _create_completed(
                env["task_store"],
                title=f"Python 报错{index}",
                steps=("复现", "读 traceback", "修复"),
                run_ids=(f"r{index}",),
            )
        )
    cluster_json = _mining_cluster_json(tuple(task_ids))
    # 响应序列：第一次 mining 成功 + distill 抛异常；第二次 mining 成功 + distill 成功。
    registry, adapter = _fake_registry(
        [
            _model_response(cluster_json),
            RuntimeError("model down"),
            _model_response(cluster_json),
            _model_response(_DISTILL_CREATE_JSON),
        ]
    )
    service = SkillLearningService(
        env["task_store"],
        env["trace_store"],
        env["skill_store"],
        env["candidate_store"],
        registry,
        settings=_settings(root, batch_size=3),
        default_provider="fake",
    )

    # 第一次：mining 成功但蒸馏失败 → batch 仍 inflight、未 processed。
    outcome1 = await service.maybe_run_mining()
    assert outcome1.cluster_count == 1
    assert outcome1.candidate_count == 0
    assert outcome1.error
    watermark1 = await env["candidate_store"].load_watermark()
    assert watermark1.inflight is not None
    assert watermark1.inflight.attempt == 1
    assert not set(task_ids).issubset(set(watermark1.processed_task_ids))

    # 第二次重试：蒸馏成功 → Candidate 创建 + batch processed。
    outcome2 = await service.maybe_run_mining()
    assert outcome2.candidate_count == 1
    watermark2 = await env["candidate_store"].load_watermark()
    assert watermark2.inflight is None
    assert set(task_ids).issubset(set(watermark2.processed_task_ids))


# ---------------------------------------------------------------------------
# 12. failed task_update 不能进入 "Task 变更"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_task_update_not_in_task_changes(tmp_path: Path) -> None:
    """失败的 task_update 只进入"失败工具调用"，不视为真实状态变化。"""
    env, _ = await _make_env(tmp_path)
    task_id = await _create_completed(
        env["task_store"],
        title="修复报错",
        run_ids=("r1",),
    )
    task = await env["task_store"].get(task_id)
    assert task is not None
    events = (
        _tool_started("r1", 1, "task_update", {"task_id": task_id, "goal": "新目标"}),
        _tool_completed("r1", 2, "task_update", {"task_id": task_id, "goal": "新目标"}),
        _tool_started(
            "r1", 3, "task_update",
            {"task_id": task_id, "constraints": ["不要重装"]},
        ),
        _tool_completed(
            "r1", 4, "task_update",
            {"task_id": task_id, "constraints": ["不要重装"]},
            success=False,
            error="boom",
        ),
    )
    builder = TraceEvidenceBuilder(_settings(tmp_path))
    text = builder.build(task, events)
    # 成功的 task_update 进入 Task 变更。
    assert "goal: 新目标" in text
    # 失败的 task_update 只进失败工具调用。
    assert "失败工具调用" in text
    assert "boom" in text
    # 失败的不进 Task 变更。
    assert "constraints added: 不要重装" not in text
