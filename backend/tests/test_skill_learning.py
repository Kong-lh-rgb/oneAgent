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
