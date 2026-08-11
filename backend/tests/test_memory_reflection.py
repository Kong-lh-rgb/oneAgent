"""Post-Run Memory Reflection 的离线测试。"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.agent.events import AgentEventType, InMemoryEventHandler
from app.agent.result import AgentStopReason
from app.agent.runtime import AgentRuntime
from app.memory import (
    MemoryManager,
    MemoryReflectionConfig,
    MemoryReflectionInput,
    PostRunMemoryReflector,
    ReflectionAction,
    register_memory_tools,
)
from app.models.adapter import ModelAdapter
from app.models.config import ModelSettings, ProviderConfig
from app.models.registry import ModelAdapterRegistry
from app.models.types import (
    ApiStyle,
    Message,
    MessageRole,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    ToolCall,
)
from app.tools.registry import ToolRegistry


class FakeAdapter(ModelAdapter):
    """按顺序返回固定结果的离线模型适配器。"""

    def __init__(
        self,
        config: ProviderConfig,
        responses: Sequence[ModelResponse | Exception],
        *,
        delay_seconds: float = 0.0,
    ) -> None:
        super().__init__(config)
        self.responses = list(responses)
        self.delay_seconds = delay_seconds
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def close(self) -> None:
        pass


def _config(provider: str, model: str) -> ProviderConfig:
    return ProviderConfig(
        provider=provider,
        model=model,
        api_key=SecretStr("offline-test-key"),
        api_style=ApiStyle.CHAT_COMPLETIONS,
    )


def _response(
    content: str,
    *,
    provider: str = "reflect",
    model: str = "reflection-model",
    usage: ModelUsage | None = None,
) -> ModelResponse:
    return ModelResponse(
        id="offline-response",
        provider=provider,
        model=model,
        message=Message(role=MessageRole.ASSISTANT, content=content),
        usage=usage or ModelUsage(),
    )


def _registry(**adapters: FakeAdapter) -> ModelAdapterRegistry:
    registry = ModelAdapterRegistry(ModelSettings(_env_file=None))
    for provider, adapter in adapters.items():
        registry.register(
            provider,
            lambda _, current=adapter: current,
            config=adapter.config,
        )
    return registry


def _input(
    *,
    recalled_memory_ids: tuple[str, ...] = (),
) -> MemoryReflectionInput:
    return MemoryReflectionInput(
        run_id="run-1",
        conversation_id="conversation-1",
        user_input="我们决定继续使用 Markdown 长期记忆。",
        final_answer="架构调整已经完成。",
        recalled_memory_ids=recalled_memory_ids,
    )


def _reflection_config(**overrides: object) -> MemoryReflectionConfig:
    values: dict[str, object] = {
        "provider": "reflect",
        "model": "reflection-model",
    }
    values.update(overrides)
    return MemoryReflectionConfig(
        _env_file=None,
        **values,
    )


async def _manager(path: Path, *, max_active: int = 25) -> MemoryManager:
    manager = MemoryManager(path, max_active=max_active)
    await manager.initialize()
    return manager


@pytest.mark.asyncio
async def test_reflection_noop_does_not_mutate_store(tmp_path: Path) -> None:
    manager = await _manager(tmp_path / "memory")
    adapter = FakeAdapter(
        _config("reflect", "reflection-model"),
        [_response('{"action":"none","reason":"只有临时任务信息"}')],
    )
    reflector = PostRunMemoryReflector(
        _registry(reflect=adapter),
        manager,
        config=_reflection_config(),
    )

    outcome = await reflector.run(_input())

    assert outcome.action is ReflectionAction.NONE
    assert outcome.error is None
    assert await manager.store.count_active() == 0


@pytest.mark.asyncio
async def test_reflection_create_uses_manager_and_rebuilds_index(
    tmp_path: Path,
) -> None:
    manager = await _manager(tmp_path / "memory")
    decision = (
        '{"action":"create","title":"Memory 架构决定",'
        '"summary":"OneAgent 使用 Markdown Memory 与运行后反思",'
        '"content":"普通长期记忆由 Post-Run Reflector 沉淀。",'
        '"reason":"这是跨会话仍有价值的架构决定"}'
    )
    adapter = FakeAdapter(
        _config("reflect", "reflection-model"),
        [_response(decision)],
    )
    reflector = PostRunMemoryReflector(
        _registry(reflect=adapter),
        manager,
        config=_reflection_config(),
    )

    outcome = await reflector.run(_input())

    assert outcome.action is ReflectionAction.CREATE
    assert outcome.memory_id == "M001"
    record = await manager.store.load("M001")
    assert record is not None
    assert "Post-Run Reflector" in record.content
    assert "OneAgent 使用 Markdown Memory" in (
        await manager.index.load() or ""
    )


@pytest.mark.asyncio
async def test_reflection_update_uses_manager_and_rebuilds_index(
    tmp_path: Path,
) -> None:
    manager = await _manager(tmp_path / "memory")
    record = await manager.create(
        title="Memory 架构决定",
        summary="Markdown Memory 架构",
        content="旧决定",
    )
    (manager.memory_dir / "INDEX.md").write_text("stale", encoding="utf-8")
    decision = (
        '{"action":"update","memory_id":"m001",'
        '"content":"新决定：普通记忆改由 Post-Run Reflector 写入。",'
        '"reason":"本轮改变了已有架构决定"}'
    )
    adapter = FakeAdapter(
        _config("reflect", "reflection-model"),
        [_response(decision)],
    )
    reflector = PostRunMemoryReflector(
        _registry(reflect=adapter),
        manager,
        config=_reflection_config(),
    )

    outcome = await reflector.run(_input(recalled_memory_ids=(record.id,)))

    assert outcome.action is ReflectionAction.UPDATE
    assert outcome.memory_id == record.id
    updated = await manager.store.load(record.id)
    assert updated is not None
    assert "Post-Run Reflector" in updated.content
    assert "Markdown Memory 架构" in (await manager.index.load() or "")


@pytest.mark.asyncio
async def test_reflection_update_requires_current_run_memory_read(
    tmp_path: Path,
) -> None:
    manager = await _manager(tmp_path / "memory")
    record = await manager.create(
        title="已有决定",
        summary="只能从 Index 看到的 cue",
        content="不能被猜测覆盖的完整正文",
    )
    before = (manager.memory_dir / "active" / f"{record.id}.md").read_text(
        encoding="utf-8"
    )
    adapter = FakeAdapter(
        _config("reflect", "reflection-model"),
        [
            _response(
                '{"action":"update","memory_id":"M001",'
                '"content":"模型根据 cue 猜测的替代正文",'
                '"reason":"错误地尝试更新"}'
            )
        ],
    )
    reflector = PostRunMemoryReflector(
        _registry(reflect=adapter),
        manager,
        config=_reflection_config(),
    )

    outcome = await reflector.run(_input())

    assert outcome.action is ReflectionAction.UPDATE
    assert outcome.error is not None
    assert outcome.memory_id == record.id
    assert "memory.read success" in outcome.error
    assert (
        manager.memory_dir / "active" / f"{record.id}.md"
    ).read_text(encoding="utf-8") == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("responses", "delay_seconds", "timeout_seconds", "error_text"),
    [
        ([RuntimeError("provider unavailable")], 0.0, 1.0, "provider unavailable"),
        ([_response("not-json")], 0.0, 1.0, "ValidationError"),
        ([_response('{"action":"none","reason":"noop"}')], 0.05, 0.01, "TimeoutError"),
    ],
)
async def test_reflection_failures_are_isolated(
    tmp_path: Path,
    responses: Sequence[ModelResponse | Exception],
    delay_seconds: float,
    timeout_seconds: float,
    error_text: str,
) -> None:
    manager = await _manager(tmp_path / "memory")
    adapter = FakeAdapter(
        _config("reflect", "reflection-model"),
        responses,
        delay_seconds=delay_seconds,
    )
    reflector = PostRunMemoryReflector(
        _registry(reflect=adapter),
        manager,
        config=_reflection_config(timeout_seconds=timeout_seconds),
    )

    outcome = await reflector.run(_input())

    assert outcome.triggered is True
    assert outcome.error is not None and error_text in outcome.error
    assert await manager.store.count_active() == 0


@pytest.mark.asyncio
async def test_runtime_final_answer_triggers_independent_reflection_model(
    tmp_path: Path,
) -> None:
    manager = await _manager(tmp_path / "memory")
    main = FakeAdapter(
        _config("main", "main-model"),
        [_response("主任务完成", provider="main", model="main-model")],
    )
    reflection_usage = ModelUsage(input_tokens=9, output_tokens=4, total_tokens=13)
    reflect = FakeAdapter(
        _config("reflect", "adapter-default"),
        [
            _response(
                '{"action":"none","reason":"没有耐久的新信息"}',
                usage=reflection_usage,
            )
        ],
    )
    registry = _registry(main=main, reflect=reflect)
    reflector = PostRunMemoryReflector(
        registry,
        manager,
        config=_reflection_config(
            model="cheap-memory-model",
            max_output_tokens=321,
            temperature=0.2,
        ),
        default_provider="main",
        default_model="main-model",
    )
    events = InMemoryEventHandler()
    runtime = AgentRuntime(
        registry,
        ToolRegistry(),
        provider="main",
        model="main-model",
        memory_manager=manager,
        memory_reflector=reflector,
    )

    result = await runtime.run("完成当前任务", event_handler=events)

    assert result.ok is True
    assert len(main.requests) == 1
    assert len(reflect.requests) == 1
    request = reflect.requests[0]
    assert request.model == "cheap-memory-model"
    assert request.max_output_tokens == 321
    assert request.temperature == 0.2
    assert request.tools == ()
    assert [event.type for event in events.events][-3:] == [
        AgentEventType.MEMORY_REFLECTION_STARTED,
        AgentEventType.MEMORY_REFLECTION_COMPLETED,
        AgentEventType.AGENT_COMPLETED,
    ]
    completed = events.events[-2]
    assert completed.provider == "reflect"
    assert completed.model == "cheap-memory-model"
    assert completed.reflection_action == "none"
    assert completed.usage == reflection_usage


@pytest.mark.asyncio
async def test_runtime_successful_memory_read_authorizes_reflection_update(
    tmp_path: Path,
) -> None:
    manager = await _manager(tmp_path / "memory")
    record = await manager.create(
        title="项目方向",
        summary="OneAgent 的长期记忆方向",
        content="旧方向",
    )
    main = FakeAdapter(
        _config("main", "main-model"),
        [
            ModelResponse(
                id="read-response",
                provider="main",
                model="main-model",
                message=Message(
                    role=MessageRole.ASSISTANT,
                    tool_calls=(
                        ToolCall(
                            id="read-memory-1",
                            name="memory.read",
                            arguments={"memory_id": record.id},
                        ),
                    ),
                ),
            ),
            _response("已完成架构调整", provider="main", model="main-model"),
        ],
    )
    reflect = FakeAdapter(
        _config("reflect", "reflection-model"),
        [
            _response(
                '{"action":"update","memory_id":"M001",'
                '"content":"新方向：普通记忆由运行后反思模型沉淀。",'
                '"reason":"本轮更新了已有架构决定"}'
            )
        ],
    )
    registry = _registry(main=main, reflect=reflect)
    tools = ToolRegistry()
    register_memory_tools(tools, manager)
    runtime = AgentRuntime(
        registry,
        tools,
        provider="main",
        memory_manager=manager,
        memory_reflector=PostRunMemoryReflector(
            registry,
            manager,
            config=_reflection_config(),
        ),
    )

    result = await runtime.run("读取旧决定并完成调整")

    assert result.ok is True
    updated = await manager.store.load(record.id)
    assert updated is not None
    assert "运行后反思模型" in updated.content


@pytest.mark.asyncio
async def test_reflection_provider_only_uses_that_adapters_default_model(
    tmp_path: Path,
) -> None:
    manager = await _manager(tmp_path / "memory")
    reflect = FakeAdapter(
        _config("reflect", "provider-default-model"),
        [_response('{"action":"none","reason":"没有新记忆"}')],
    )
    registry = _registry(reflect=reflect)
    reflector = PostRunMemoryReflector(
        registry,
        manager,
        config=MemoryReflectionConfig(
            _env_file=None,
            provider="reflect",
            model=None,
        ),
        default_provider="main",
        default_model="main-model-must-not-leak",
    )

    outcome = await reflector.run(_input())

    assert outcome.error is None
    assert outcome.model == "provider-default-model"
    assert reflect.requests[0].model == "provider-default-model"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [RuntimeError("provider failed"), ValueError("bad")],
)
async def test_runtime_reflection_failure_does_not_change_success_result(
    tmp_path: Path,
    failure: Exception,
) -> None:
    manager = await _manager(tmp_path / "memory")
    main = FakeAdapter(
        _config("main", "main-model"),
        [_response("最终答案", provider="main", model="main-model")],
    )
    reflection_response: ModelResponse | Exception
    if isinstance(failure, ValueError):
        reflection_response = _response("invalid-json")
    else:
        reflection_response = failure
    reflect = FakeAdapter(
        _config("reflect", "reflection-model"),
        [reflection_response],
    )
    registry = _registry(main=main, reflect=reflect)
    events = InMemoryEventHandler()
    runtime = AgentRuntime(
        registry,
        ToolRegistry(),
        provider="main",
        memory_manager=manager,
        memory_reflector=PostRunMemoryReflector(
            registry,
            manager,
            config=_reflection_config(),
        ),
    )

    result = await runtime.run("正常任务", event_handler=events)

    assert result.ok is True
    assert result.content == "最终答案"
    assert events.events[-2].type is AgentEventType.MEMORY_REFLECTION_FAILED
    assert events.events[-2].reflection_error is not None
    assert events.events[-1].type is AgentEventType.AGENT_COMPLETED


@pytest.mark.asyncio
async def test_runtime_model_error_skips_reflection(tmp_path: Path) -> None:
    manager = await _manager(tmp_path / "memory")
    main = FakeAdapter(
        _config("main", "main-model"),
        [RuntimeError("main failed")],
    )
    reflect = FakeAdapter(
        _config("reflect", "reflection-model"),
        [_response('{"action":"none","reason":"unused"}')],
    )
    registry = _registry(main=main, reflect=reflect)
    events = InMemoryEventHandler()
    runtime = AgentRuntime(
        registry,
        ToolRegistry(),
        provider="main",
        memory_manager=manager,
        memory_reflector=PostRunMemoryReflector(
            registry,
            manager,
            config=_reflection_config(),
        ),
    )

    result = await runtime.run("会失败", event_handler=events)

    assert result.stop_reason is AgentStopReason.MODEL_ERROR
    assert reflect.requests == []
    assert events.events[-2].type is AgentEventType.MEMORY_REFLECTION_SKIPPED
    assert events.events[-1].type is AgentEventType.AGENT_FAILED


@pytest.mark.asyncio
async def test_runtime_max_steps_skips_reflection(tmp_path: Path) -> None:
    manager = await _manager(tmp_path / "memory")
    main = FakeAdapter(
        _config("main", "main-model"),
        [
            ModelResponse(
                id="tool-response",
                provider="main",
                model="main-model",
                message=Message(
                    role=MessageRole.ASSISTANT,
                    tool_calls=(ToolCall(id="missing-1", name="missing"),),
                ),
            )
        ],
    )
    reflect = FakeAdapter(
        _config("reflect", "reflection-model"),
        [_response('{"action":"none","reason":"unused"}')],
    )
    registry = _registry(main=main, reflect=reflect)
    events = InMemoryEventHandler()
    runtime = AgentRuntime(
        registry,
        ToolRegistry(),
        provider="main",
        max_steps=1,
        memory_manager=manager,
        memory_reflector=PostRunMemoryReflector(
            registry,
            manager,
            config=_reflection_config(),
        ),
    )

    result = await runtime.run("需要更多步骤", event_handler=events)

    assert result.stop_reason is AgentStopReason.MAX_STEPS
    assert reflect.requests == []
    assert events.events[-2].type is AgentEventType.MEMORY_REFLECTION_SKIPPED
    assert events.events[-1].type is AgentEventType.AGENT_FAILED


@pytest.mark.asyncio
async def test_reflection_26th_create_uses_existing_maintenance_signal(
    tmp_path: Path,
) -> None:
    manager = await _manager(tmp_path / "memory")
    for index in range(25):
        await manager.create(
            title=f"记忆 {index}",
            summary=f"cue {index}",
            content="正文",
        )
    adapter = FakeAdapter(
        _config("reflect", "reflection-model"),
        [
            _response(
                '{"action":"create","title":"第 26 条",'
                '"summary":"触发现有维护信号","content":"新正文",'
                '"reason":"耐久信息"}'
            )
        ],
    )
    reflector = PostRunMemoryReflector(
        _registry(reflect=adapter),
        manager,
        config=_reflection_config(),
    )

    outcome = await reflector.run(_input())

    assert outcome.action is ReflectionAction.CREATE
    assert await manager.store.count_active() == 26
    assert outcome.maintenance_required is True
    assert outcome.retention_candidate_ids
