"""Post-Run Background Processing：final answer 后不阻塞 + 后台生命周期。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.agent.events import AgentEventType, InMemoryEventHandler
from app.agent.post_run_processor import PostRunProcessor
from app.agent.runtime import AgentRuntime
from app.application import Application
from app.memory import (
    MemoryMaintenanceConfig,
    MemoryMaintenanceReflector,
    MemoryManager,
    MemoryReflectionConfig,
    PostRunMemoryReflector,
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
from app.skill_learning import SkillLearningSettings
from app.tools.registry import ToolRegistry


class FakeAdapter(ModelAdapter):
    """可选的 gate 卡住 reflect 调用（模拟 slow memory reflector）。"""

    def __init__(
        self,
        config: ProviderConfig,
        responses: list[ModelResponse | Exception],
        *,
        gate: asyncio.Event | None = None,
    ) -> None:
        super().__init__(config)
        self.responses = list(responses)
        self.gate = gate
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if self.gate is not None:
            await self.gate.wait()
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
) -> ModelResponse:
    return ModelResponse(
        id="offline-response",
        provider=provider,
        model=model,
        message=Message(role=MessageRole.ASSISTANT, content=content),
        usage=ModelUsage(),
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


async def _manager(path: Path) -> MemoryManager:
    manager = MemoryManager(path)
    await manager.initialize()
    return manager


def _reflection_config(**overrides: object) -> MemoryReflectionConfig:
    return MemoryReflectionConfig(_env_file=None, **overrides)


def _create_json(title: str = "新项目决定", content: str = "决定正文") -> str:
    return (
        f'{{"action":"create","title":"{title}","summary":"决定摘要",'
        f'"content":"{content}","reason":"值得长期记忆"}}'
    )


def _runtime(
    manager: MemoryManager,
    *,
    main_responses: list[ModelResponse | Exception],
    reflect_responses: list[ModelResponse | Exception],
    processor: PostRunProcessor,
    gate: asyncio.Event | None = None,
    tools: ToolRegistry | None = None,
) -> tuple[AgentRuntime, InMemoryEventHandler, FakeAdapter]:
    main = FakeAdapter(
        _config("main", "main-model"),
        main_responses,
    )
    reflect = FakeAdapter(
        _config("reflect", "reflection-model"),
        reflect_responses,
        gate=gate,
    )
    registry = _registry(main=main, reflect=reflect)
    events = InMemoryEventHandler()
    runtime = AgentRuntime(
        registry,
        tools or ToolRegistry(),
        provider="main",
        memory_manager=manager,
        memory_reflector=PostRunMemoryReflector(
            registry,
            config=_reflection_config(),
            default_provider="reflect",
            default_model="reflection-model",
        ),
        post_run_submit=processor.submit,
    )
    return runtime, events, reflect


async def _wait_active_zero(processor: PostRunProcessor, timeout: float = 4.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while processor.active_count > 0:
        if loop.time() > deadline:
            raise AssertionError("background job did not finish in time")
        await asyncio.sleep(0.01)


# ---------------------------------------------------------------------------
# 1) final answer 后不等待 slow reflector + 事件顺序 + 后台 CREATE 生效
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_completed_before_slow_background_reflection(tmp_path: Path) -> None:
    manager = await _manager(tmp_path / "memory")
    gate = asyncio.Event()
    processor = PostRunProcessor()
    runtime, events, _ = _runtime(
        manager,
        main_responses=[_response("最终答案", provider="main", model="main-model")],
        reflect_responses=[_response(_create_json())],
        processor=processor,
        gate=gate,
    )

    result = await runtime.run("完成当前任务", event_handler=events)

    # critical path 立即完成，reflection 仍在后台（gate 未放行）
    assert result.ok is True
    assert processor.active_count == 1
    types = [event.type for event in events.events]
    assert AgentEventType.AGENT_COMPLETED in types
    assert AgentEventType.MEMORY_REFLECTION_STARTED not in types

    gate.set()
    await _wait_active_zero(processor)
    await processor.close()

    # 后台 reflection 最终 CREATE 生效
    assert await manager.active_count() == 1
    types = [event.type for event in events.events]
    # 事件顺序：agent_completed 在 memory_reflection_started 之前
    assert types.index(AgentEventType.AGENT_COMPLETED) < types.index(
        AgentEventType.MEMORY_REFLECTION_STARTED
    )
    assert AgentEventType.MEMORY_REFLECTION_COMPLETED in types


# ---------------------------------------------------------------------------
# 2) 后台 UPDATE 仍遵守 revision 语义
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_background_reflection_update_preserves_revision_semantics(
    tmp_path: Path,
) -> None:
    manager = await _manager(tmp_path / "memory")
    record = await manager.create(
        title="项目方向",
        summary="旧方向",
        content="初始正文",
    )
    manager_ref = manager
    tools = ToolRegistry()
    register_memory_tools(tools, manager)
    processor = PostRunProcessor()

    def _update_runtime() -> AgentRuntime:
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
                                name="memory_read",
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
                    '"title":"项目长期记忆方向",'
                    '"summary":"普通记忆由运行后反思模型沉淀",'
                    '"content":"新方向：运行后反思模型沉淀。",'
                    '"reason":"本轮更新了已有架构决定"}'
                )
            ],
        )
        registry = _registry(main=main, reflect=reflect)
        return AgentRuntime(
            registry,
            tools,
            provider="main",
            memory_manager=manager_ref,
            memory_reflector=PostRunMemoryReflector(
                registry,
                config=_reflection_config(),
                default_provider="reflect",
                default_model="reflection-model",
            ),
            post_run_submit=processor.submit,
        )

    runtime = _update_runtime()
    events = InMemoryEventHandler()
    result = await runtime.run("读取旧决定并完成调整", event_handler=events)

    assert result.ok is True
    await _wait_active_zero(processor)
    await processor.close()

    updated = await manager.store.load(record.id)
    assert updated is not None
    assert updated.title == "项目长期记忆方向"
    assert "运行后反思模型沉淀" in updated.content
    # revision 递增（旧 revision 小于新 revision）
    assert updated.revision > record.revision
    assert "运行后反思模型沉淀" in (await manager.index.load() or "")


# ---------------------------------------------------------------------------
# 3) reflection 异常 → Run 保持 completed，后台任务被干净消费（无 unhandled）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_background_reflection_exception_keeps_run_completed(
    tmp_path: Path,
) -> None:
    manager = await _manager(tmp_path / "memory")
    processor = PostRunProcessor()
    runtime, events, _ = _runtime(
        manager,
        main_responses=[_response("最终答案", provider="main", model="main-model")],
        reflect_responses=[RuntimeError("reflector boom")],
        processor=processor,
    )

    result = await runtime.run("正常任务", event_handler=events)

    assert result.ok is True
    await _wait_active_zero(processor)
    await processor.close()
    types = [event.type for event in events.events]
    assert AgentEventType.AGENT_COMPLETED in types
    assert AgentEventType.MEMORY_REFLECTION_FAILED in types
    assert types.index(AgentEventType.AGENT_COMPLETED) < types.index(
        AgentEventType.MEMORY_REFLECTION_FAILED
    )


# ---------------------------------------------------------------------------
# 4) memory maintenance 仍在后台 reflection 后执行
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_maintenance_runs_after_background_reflection(tmp_path: Path) -> None:
    manager = await _manager(tmp_path / "memory")
    for index in range(1, manager.max_active + 2):
        await manager.store.create(
            title=f"记忆 {index}",
            summary=f"cue {index}",
            content=f"完整正文 {index}",
        )
    main = FakeAdapter(
        _config("main", "main-model"),
        [_response("主任务完成", provider="main", model="main-model")],
    )
    reflect = FakeAdapter(
        _config("reflect", "reflection-model"),
        [_response(_create_json("第 26 条", "新的长期正文"))],
    )
    maintain = FakeAdapter(
        _config("maintain", "maintenance-model"),
        [
            _response(
                '{"action":"archive","memory_id":"M001","reason":"已经过时"}',
                provider="maintain",
                model="maintenance-model",
            )
        ],
    )
    registry = _registry(main=main, reflect=reflect, maintain=maintain)
    events = InMemoryEventHandler()
    processor = PostRunProcessor()
    runtime = AgentRuntime(
        registry,
        ToolRegistry(),
        provider="main",
        memory_manager=manager,
        memory_reflector=PostRunMemoryReflector(
            registry,
            config=_reflection_config(),
            default_provider="reflect",
            default_model="reflection-model",
        ),
        memory_maintenance_reflector=MemoryMaintenanceReflector(
            registry,
            config=MemoryMaintenanceConfig(
                _env_file=None,
                provider="maintain",
                model="maintenance-model",
            ),
            default_provider="reflect",
            default_model="reflection-model",
        ),
        post_run_submit=processor.submit,
    )

    result = await runtime.run("普通请求", event_handler=events)

    assert result.ok is True
    await _wait_active_zero(processor)
    await processor.close()
    assert await manager.active_count() <= manager.max_active
    types = [event.type for event in events.events]
    # agent_completed 先于 post-run 维护
    assert types.index(AgentEventType.AGENT_COMPLETED) < types.index(
        AgentEventType.MEMORY_MAINTENANCE_STARTED
    )
    assert AgentEventType.MEMORY_MAINTENANCE_COMPLETED in types


# ---------------------------------------------------------------------------
# 5) PostRunProcessor / Application.close：drain 与超时 cancel 不泄漏
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_run_processor_close_drains_and_cancels() -> None:
    processor = PostRunProcessor(drain_timeout=0.2)
    done = asyncio.Event()

    async def fast() -> None:
        await asyncio.sleep(0.01)
        done.set()

    assert processor.submit(fast) is True
    await processor.close()
    assert done.is_set()
    assert processor.active_count == 0

    # 挂起任务：超时 cancel，不遗留 pending
    processor2 = PostRunProcessor(drain_timeout=0.05)
    gate = asyncio.Event()

    async def stuck() -> None:
        await gate.wait()

    processor2.submit(stuck)
    await processor2.close()
    assert processor2.active_count == 0
    assert processor2.submit(stuck) is False  # closed 后拒绝新任务


@pytest.mark.asyncio
async def test_application_close_drains_post_run(tmp_path: Path) -> None:
    registry = _registry(
        main=FakeAdapter(_config("main", "main-model"), []),
    )
    app = Application(
        provider="main",
        model="main-model",
        database=tmp_path / "vesta.db",
        tasks_dir=tmp_path / "tasks",
        mcp_config=tmp_path / "mcp.json",
        memory_dir=tmp_path / "memory",
        skills_user_dir=tmp_path / "skills-user",
        skills_project_dir=tmp_path / "skills-project",
        registry=registry,
        memory_reflection_config=MemoryReflectionConfig(
            _env_file=None, enabled=False
        ),
        memory_maintenance_config=MemoryMaintenanceConfig(
            _env_file=None, enabled=False
        ),
        skill_learning_settings=SkillLearningSettings(
            _env_file=None,
            skill_learning_enabled=False,
            skill_learning_data_dir=tmp_path / "skill-learning",
        ),
    )
    await app.start()
    gate = asyncio.Event()

    async def slow() -> None:
        await gate.wait()

    assert app.post_run_processor.submit(slow) is True
    assert app.post_run_processor.active_count == 1
    gate.set()
    await app.close()
    assert app.post_run_processor.active_count == 0


# ---------------------------------------------------------------------------
# 6) 下一 Run 不被上一 Run 的后台 reflection 阻塞
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_next_run_not_blocked_by_previous_slow_reflection(tmp_path: Path) -> None:
    manager = await _manager(tmp_path / "memory")
    gate = asyncio.Event()
    processor = PostRunProcessor()
    runtime, events, _ = _runtime(
        manager,
        main_responses=[
            _response("第一轮回答", provider="main", model="main-model"),
            _response("第二轮回答", provider="main", model="main-model"),
        ],
        reflect_responses=[
            _response(_create_json("第一轮", "内容一")),
            _response(_create_json("第二轮", "内容二")),
        ],
        processor=processor,
        gate=gate,
    )

    first = await runtime.run("第一个任务", event_handler=events)
    assert first.ok is True
    assert processor.active_count == 1

    # 第二个 Run 不被第一个的后台 reflection 阻塞（critical path 已返回）
    second = await runtime.run("第二个任务", event_handler=events)
    assert second.ok is True
    assert processor.active_count == 2

    gate.set()
    await _wait_active_zero(processor)
    await processor.close()
    assert await manager.active_count() == 2
