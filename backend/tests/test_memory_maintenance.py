"""Post-Run Memory Capacity Maintenance 的离线闭环测试。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.agent.events import AgentEventType, InMemoryEventHandler
from app.agent.runtime import AgentRuntime
from app.memory import (
    MemoryMaintenanceConfig,
    MemoryMaintenanceReflector,
    MemoryManager,
    MemoryReflectionConfig,
    PostRunMemoryReflector,
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
)
from app.tools.registry import ToolRegistry


class FakeAdapter(ModelAdapter):
    """支持延迟与返回前回调的离线模型适配器。"""

    def __init__(
        self,
        config: ProviderConfig,
        responses: Sequence[ModelResponse | Exception],
        *,
        delay_seconds: float = 0.0,
        before_return: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__(config)
        self.responses = list(responses)
        self.delay_seconds = delay_seconds
        self.before_return = before_return
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.before_return is not None:
            await self.before_return()
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def close(self) -> None:
        pass


def _provider_config(provider: str, model: str) -> ProviderConfig:
    return ProviderConfig(
        provider=provider,
        model=model,
        api_key=SecretStr("offline-test-key"),
        api_style=ApiStyle.CHAT_COMPLETIONS,
    )


def _response(
    content: str,
    *,
    provider: str,
    model: str,
) -> ModelResponse:
    return ModelResponse(
        id=f"{provider}-response",
        provider=provider,
        model=model,
        message=Message(role=MessageRole.ASSISTANT, content=content),
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


def _reflection_config() -> MemoryReflectionConfig:
    return MemoryReflectionConfig(
        _env_file=None,
        provider="reflect",
        model="reflection-model",
    )


def _maintenance_config(**overrides: object) -> MemoryMaintenanceConfig:
    values: dict[str, object] = {
        "provider": "maintain",
        "model": "maintenance-model",
    }
    values.update(overrides)
    return MemoryMaintenanceConfig(_env_file=None, **values)


async def _manager(path: Path) -> MemoryManager:
    manager = MemoryManager(path)
    await manager.initialize()
    return manager


async def _fill(manager: MemoryManager, count: int) -> None:
    for index in range(1, count + 1):
        values = {
            "title": f"记忆 {index}",
            "summary": f"cue {index}",
            "content": f"完整正文 {index}",
        }
        if index <= manager.max_active:
            await manager.create(**values)
        else:
            # 模拟旧版本或人工导入留下的超容量数据。
            await manager.store.create(**values)


def _runtime(
    manager: MemoryManager,
    *,
    reflection_content: str,
    maintenance_responses: Sequence[ModelResponse | Exception],
    maintenance_config: MemoryMaintenanceConfig | None = None,
    maintenance_delay: float = 0.0,
    maintenance_before_return: Callable[[], Awaitable[None]] | None = None,
) -> tuple[AgentRuntime, InMemoryEventHandler, FakeAdapter, FakeAdapter]:
    main = FakeAdapter(
        _provider_config("main", "main-model"),
        [_response("主任务完成", provider="main", model="main-model")],
    )
    reflect = FakeAdapter(
        _provider_config("reflect", "reflection-model"),
        [
            _response(
                reflection_content,
                provider="reflect",
                model="reflection-model",
            )
        ],
    )
    maintain = FakeAdapter(
        _provider_config("maintain", "maintenance-model"),
        maintenance_responses,
        delay_seconds=maintenance_delay,
        before_return=maintenance_before_return,
    )
    registry = _registry(main=main, reflect=reflect, maintain=maintain)
    events = InMemoryEventHandler()
    runtime = AgentRuntime(
        registry,
        ToolRegistry(),
        provider="main",
        memory_manager=manager,
        memory_reflector=PostRunMemoryReflector(
            registry,
            config=_reflection_config(),
        ),
        memory_maintenance_reflector=MemoryMaintenanceReflector(
            registry,
            config=maintenance_config or _maintenance_config(),
            default_provider="reflect",
            default_model="reflection-model",
        ),
    )
    return runtime, events, reflect, maintain


def _create_decision() -> str:
    return (
        '{"action":"create","title":"第 26 条",'
        '"summary":"新的长期架构决定","content":"新的长期正文",'
        '"reason":"这是耐久的跨会话信息"}'
    )


def _archive_decision(memory_id: str = "M001") -> ModelResponse:
    return _response(
        '{"action":"archive","memory_id":"'
        f'{memory_id}","reason":"该记忆已经过时"}}',
        provider="maintain",
        model="maintenance-model",
    )


def _defer_decision() -> ModelResponse:
    return _response(
        '{"action":"defer","memory_id":null,'
        '"reason":"当前候选仍然具有独立价值"}',
        provider="maintain",
        model="maintenance-model",
    )


@pytest.mark.asyncio
async def test_full_capacity_archives_then_creates_without_exceeding_limit(
    tmp_path: Path,
) -> None:
    manager = await _manager(tmp_path / "memory")
    await _fill(manager, 25)
    runtime, events, _, maintain = _runtime(
        manager,
        reflection_content=_create_decision(),
        maintenance_responses=[_archive_decision()],
        maintenance_config=_maintenance_config(
            max_output_tokens=456,
            temperature=0.1,
        ),
    )

    result = await runtime.run("完成新的长期决定", event_handler=events)

    assert result.ok is True
    assert await manager.active_count() == 25
    assert (manager.memory_dir / "archive" / "M001.md").is_file()
    assert (manager.memory_dir / "active" / "M026.md").is_file()
    assert len(maintain.requests) == 1
    assert maintain.requests[0].max_output_tokens == 456
    assert maintain.requests[0].temperature == 0.1
    assert "完整正文 1" in (maintain.requests[0].messages[-1].content or "")
    event_types = [event.type for event in events.events]
    # agent_completed 先于 memory reflection（post-run housekeeping 不阻塞 Run）。
    assert event_types.index(AgentEventType.AGENT_COMPLETED) < event_types.index(
        AgentEventType.MEMORY_REFLECTION_STARTED
    )
    assert AgentEventType.MEMORY_REFLECTION_COMPLETED in event_types
    assert AgentEventType.MEMORY_MAINTENANCE_COMPLETED in event_types
    maintenance_event = next(
        event
        for event in events.events
        if event.type is AgentEventType.MEMORY_MAINTENANCE_COMPLETED
    )
    assert maintenance_event.maintenance_memory_id == "M001"
    assert maintenance_event.maintenance_remaining_overflow == 0
    reflection_event = next(
        event
        for event in events.events
        if event.type is AgentEventType.MEMORY_REFLECTION_COMPLETED
    )
    assert reflection_event.reflection_memory_id == "M026"
    assert reflection_event.reflection_mutation_applied is True


@pytest.mark.asyncio
async def test_defer_keeps_old_memories_and_skips_create(tmp_path: Path) -> None:
    manager = await _manager(tmp_path / "memory")
    await _fill(manager, 25)
    runtime, events, _, _ = _runtime(
        manager,
        reflection_content=_create_decision(),
        maintenance_responses=[_defer_decision()],
    )

    result = await runtime.run("完成新的长期决定", event_handler=events)

    assert result.ok is True
    assert await manager.active_count() == 25
    assert not (manager.memory_dir / "active" / "M026.md").exists()
    assert not any((manager.memory_dir / "archive").iterdir())
    maintenance_event = next(
        event
        for event in events.events
        if event.type is AgentEventType.MEMORY_MAINTENANCE_COMPLETED
    )
    assert maintenance_event.maintenance_action == "defer"
    reflection_event = next(
        event
        for event in events.events
        if event.type is AgentEventType.MEMORY_REFLECTION_FAILED
    )
    assert reflection_event.reflection_mutation_applied is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("responses", "delay", "timeout", "error_text"),
    [
        ([RuntimeError("provider unavailable")], 0.0, 1.0, "provider unavailable"),
        (
            [_response("invalid-json", provider="maintain", model="maintenance-model")],
            0.0,
            1.0,
            "ValidationError",
        ),
        ([_defer_decision()], 0.05, 0.01, "TimeoutError"),
    ],
)
async def test_maintenance_failure_never_archives_or_fails_agent(
    tmp_path: Path,
    responses: Sequence[ModelResponse | Exception],
    delay: float,
    timeout: float,
    error_text: str,
) -> None:
    manager = await _manager(tmp_path / "memory")
    await _fill(manager, 25)
    runtime, events, _, _ = _runtime(
        manager,
        reflection_content=_create_decision(),
        maintenance_responses=responses,
        maintenance_config=_maintenance_config(timeout_seconds=timeout),
        maintenance_delay=delay,
    )

    result = await runtime.run("正常完成", event_handler=events)

    assert result.ok is True
    assert await manager.active_count() == 25
    assert not any((manager.memory_dir / "archive").iterdir())
    failed = next(
        event
        for event in events.events
        if event.type is AgentEventType.MEMORY_MAINTENANCE_FAILED
    )
    assert failed.maintenance_error is not None
    assert error_text in failed.maintenance_error
    event_types = [event.type for event in events.events]
    assert event_types.index(AgentEventType.AGENT_COMPLETED) < event_types.index(
        AgentEventType.MEMORY_MAINTENANCE_FAILED
    )


@pytest.mark.asyncio
async def test_maintenance_rejects_id_outside_candidate_set(tmp_path: Path) -> None:
    manager = await _manager(tmp_path / "memory")
    await _fill(manager, 25)
    runtime, events, _, _ = _runtime(
        manager,
        reflection_content=_create_decision(),
        maintenance_responses=[_archive_decision("M999")],
    )

    result = await runtime.run("正常完成", event_handler=events)

    assert result.ok is True
    assert await manager.active_count() == 25
    assert not any((manager.memory_dir / "archive").iterdir())
    failed = next(
        event
        for event in events.events
        if event.type is AgentEventType.MEMORY_MAINTENANCE_FAILED
    )
    assert failed.maintenance_memory_id == "M999"
    assert failed.maintenance_error is not None
    assert "outside the candidate set" in failed.maintenance_error


@pytest.mark.asyncio
async def test_maintenance_rejects_candidate_changed_during_model_call(
    tmp_path: Path,
) -> None:
    manager = await _manager(tmp_path / "memory")
    await _fill(manager, 25)

    async def mutate_candidate() -> None:
        await manager.update(
            "M001",
            content="并发更新后的正文",
            reason="另一个 Run 更新了候选",
        )

    runtime, events, _, _ = _runtime(
        manager,
        reflection_content=_create_decision(),
        maintenance_responses=[_archive_decision()],
        maintenance_before_return=mutate_candidate,
    )

    result = await runtime.run("正常完成", event_handler=events)

    assert result.ok is True
    assert await manager.active_count() == 25
    assert not (manager.memory_dir / "archive" / "M001.md").exists()
    current = await manager.store.load("M001")
    assert current is not None and current.content == "并发更新后的正文"
    failed = next(
        event
        for event in events.events
        if event.type is AgentEventType.MEMORY_MAINTENANCE_FAILED
    )
    assert failed.maintenance_error is not None
    assert "changed since maintenance snapshot" in failed.maintenance_error


@pytest.mark.asyncio
async def test_invalid_create_is_rejected_before_maintenance(tmp_path: Path) -> None:
    manager = await _manager(tmp_path / "memory")
    await _fill(manager, 25)
    invalid_create = (
        '{"action":"create","title":"'
        + ("过长" * 101)
        + '","summary":"cue","content":"正文","reason":"耐久信息"}'
    )
    runtime, events, _, maintain = _runtime(
        manager,
        reflection_content=invalid_create,
        maintenance_responses=[_archive_decision()],
    )

    result = await runtime.run("正常完成", event_handler=events)

    assert result.ok is True
    assert maintain.requests == []
    assert await manager.active_count() == 25
    assert not any((manager.memory_dir / "archive").iterdir())
    assert any(
        event.type is AgentEventType.MEMORY_REFLECTION_FAILED
        for event in events.events
    )


@pytest.mark.asyncio
async def test_preexisting_overflow_converges_with_bounded_actions(
    tmp_path: Path,
) -> None:
    manager = await _manager(tmp_path / "memory")
    await _fill(manager, 27)
    runtime, events, _, maintain = _runtime(
        manager,
        reflection_content='{"action":"none","reason":"没有新记忆"}',
        maintenance_responses=[
            _archive_decision("M001"),
            _archive_decision("M002"),
        ],
    )

    result = await runtime.run("普通请求", event_handler=events)

    assert result.ok is True
    assert await manager.active_count() == 25
    assert len(maintain.requests) == 2
    assert (manager.memory_dir / "archive" / "M001.md").is_file()
    assert (manager.memory_dir / "archive" / "M002.md").is_file()
    event_types = [event.type for event in events.events]
    assert event_types.index(AgentEventType.AGENT_COMPLETED) < event_types.index(
        AgentEventType.MEMORY_MAINTENANCE_STARTED
    )


@pytest.mark.asyncio
async def test_maintenance_can_recover_overflow_without_reflection(
    tmp_path: Path,
) -> None:
    manager = await _manager(tmp_path / "memory")
    await _fill(manager, 26)
    main = FakeAdapter(
        _provider_config("main", "main-model"),
        [_response("主任务完成", provider="main", model="main-model")],
    )
    maintain = FakeAdapter(
        _provider_config("maintain", "maintenance-model"),
        [_archive_decision()],
    )
    registry = _registry(main=main, maintain=maintain)
    events = InMemoryEventHandler()
    runtime = AgentRuntime(
        registry,
        ToolRegistry(),
        provider="main",
        memory_manager=manager,
        memory_maintenance_reflector=MemoryMaintenanceReflector(
            registry,
            config=_maintenance_config(),
        ),
    )

    result = await runtime.run("普通请求", event_handler=events)

    assert result.ok is True
    assert await manager.active_count() == 25
    assert len(maintain.requests) == 1
    event_types = [event.type for event in events.events]
    assert event_types.index(AgentEventType.AGENT_COMPLETED) < event_types.index(
        AgentEventType.MEMORY_MAINTENANCE_STARTED
    )


@pytest.mark.asyncio
async def test_max_actions_leaves_explicit_overflow_signal(tmp_path: Path) -> None:
    manager = await _manager(tmp_path / "memory")
    await _fill(manager, 28)
    runtime, events, _, maintain = _runtime(
        manager,
        reflection_content='{"action":"none","reason":"没有新记忆"}',
        maintenance_responses=[
            _archive_decision("M001"),
            _archive_decision("M002"),
        ],
        maintenance_config=_maintenance_config(max_actions=2),
    )

    result = await runtime.run("普通请求", event_handler=events)

    assert result.ok is True
    assert await manager.active_count() == 26
    assert len(maintain.requests) == 2
    skipped = next(
        event
        for event in events.events
        if event.type is AgentEventType.MEMORY_MAINTENANCE_SKIPPED
    )
    assert skipped.maintenance_skip_reason == "max_actions_reached"
    assert skipped.maintenance_remaining_overflow == 1


@pytest.mark.asyncio
async def test_concurrent_capacity_checked_create_never_exceeds_limit(
    tmp_path: Path,
) -> None:
    manager = await _manager(tmp_path / "memory")
    await _fill(manager, 24)

    first, second = await asyncio.gather(
        manager.create_if_capacity(title="并发 A", summary="A", content="A"),
        manager.create_if_capacity(title="并发 B", summary="B", content="B"),
    )

    assert await manager.active_count() == 25
    assert sum(record is not None for record in (first, second)) == 1
