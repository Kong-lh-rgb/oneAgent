"""Plan Mode V1 测试。

覆盖（对应需求 13 的后端清单）：
1. normal mode 默认行为不变
2. plan mode 可以读 / search（read_file / web_search）
3. plan mode 可以 task_create
4. plan mode 可以 task_update 计划内容
5. plan mode 不能执行副作用工具（write_file / shell / http_request 硬阻断）
6. plan mode 生成的 Task 为 PENDING
7. accept: PENDING → ACTIVE
8. reject: PENDING → CANCELLED
9. 非 PENDING Task 不能 accept / reject
10. ACTIVE Task 仍通过 TaskContextProvider 注入
11. PENDING Task 不作为 active task 注入
12. Run 正确记录 mode（通过 ConversationService → RunManager → store）
13. Plan Mode 仍产生正常 Trace / AgentEvent
14. Automation / Normal Run 不受影响（mode 默认 normal）
15. Plan Mode 未生成 Task 时返回明确提示

用 fake model + stub 读写工具 + 真实 Task 工具（FileTaskStore），不调用真实 API。
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from pydantic import SecretStr

from app.agent.events import AgentEventType, InMemoryEventHandler
from app.agent.runtime import AgentRuntime
from app.models.adapter import ModelAdapter
from app.models.config import ModelSettings, ProviderConfig
from app.models.registry import ModelAdapterRegistry
from app.models.types import (
    AgentMode,
    ApiStyle,
    Message,
    MessageRole,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    ToolCall,
    ToolDefinition,
    ToolPermission,
)
from app.task import (
    FileTaskStore,
    TaskContextProvider,
    TaskStatus,
    TaskStep,
    TaskStepStatus,
    register_task_tools,
)
from app.tools.base import BaseTool
from app.tools.registry import ToolRegistry


class ScriptedAdapter(ModelAdapter):
    def __init__(
        self,
        config: ProviderConfig,
        responses: Sequence[ModelResponse | Exception],
    ) -> None:
        super().__init__(config)
        self.responses = list(responses)
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def close(self) -> None:
        pass


class TrackingTool(BaseTool):
    """按名字构造的探测工具：记录调用，不做真实副作用。"""

    def __init__(
        self,
        name: str,
        *,
        permission: ToolPermission = ToolPermission.ALLOWED,
    ) -> None:
        self._name = name
        self.executions = 0
        self._permission = permission

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self._name,
            description=f"探测工具 {self._name}",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "query": {"type": "string"},
                    "command": {"type": "string"},
                    "url": {"type": "string"},
                },
            },
            permission=self._permission,
        )

    async def execute(self, arguments: dict[str, object]) -> str:
        self.executions += 1
        return f"{self._name}-ok"


def _registry(
    responses: Sequence[ModelResponse | Exception],
) -> tuple[ModelAdapterRegistry, ScriptedAdapter]:
    config = ProviderConfig(
        provider="fake",
        model="fake-model",
        api_key=SecretStr("offline-test-key"),
        api_style=ApiStyle.CHAT_COMPLETIONS,
    )
    adapter = ScriptedAdapter(config, responses)
    registry = ModelAdapterRegistry(ModelSettings(_env_file=None))
    registry.register("fake", lambda _: adapter, config=config)
    return registry, adapter


def _response(
    *,
    content: str | None = None,
    tool_calls: tuple[ToolCall, ...] = (),
) -> ModelResponse:
    return ModelResponse(
        id="fake-response",
        provider="fake",
        model="fake-model",
        message=Message(
            role=MessageRole.ASSISTANT,
            content=content,
            tool_calls=tool_calls,
        ),
        usage=ModelUsage(),
    )


def _call(name: str, arguments: dict[str, object]) -> ToolCall:
    return ToolCall(id=f"call-{name}", name=name, arguments=arguments)


_STUB_NAMES = (
    "read_file",
    "write_file",
    "list_files",
    "web_search",
    "http_request",
    "shell",
    "current_time",
    "memory_read",
)


async def _build_tools(
    tmp_path,
) -> tuple[ToolRegistry, FileTaskStore, dict[str, TrackingTool]]:
    """构造带真实 Task 工具 + stub 读写工具的注册表。"""

    tools = ToolRegistry()
    stubs: dict[str, TrackingTool] = {}
    for name in _STUB_NAMES:
        stub = TrackingTool(name)
        stubs[name] = stub
        tools.register(stub)

    task_store = FileTaskStore(tmp_path / "tasks")
    await task_store.initialize()
    register_task_tools(tools, task_store)
    return tools, task_store, stubs


async def _run(
    registry,
    tools,
    *,
    mode: AgentMode,
    task_store: FileTaskStore,
    conversation_id: str | None = "conv-1",
    handler: InMemoryEventHandler | None = None,
):
    runtime = AgentRuntime(
        registry,
        tools,
        provider="fake",
        task_context_provider=TaskContextProvider(task_store),
    )
    return await runtime.run(
        "规划任务",
        conversation_id=conversation_id,
        run_id="run-1",
        event_handler=handler,
        mode=mode,
    )


# ---------------------------------------------------------------------------
# 工具过滤机制本身
# ---------------------------------------------------------------------------


async def test_registry_plan_mode_filters_side_effect_tools(tmp_path) -> None:
    tools, _, _ = await _build_tools(tmp_path)

    normal = tools.allowed_names_for_mode(AgentMode.NORMAL)
    assert "write_file" in normal and "shell" in normal

    plan = tools.allowed_names_for_mode(AgentMode.PLAN)
    assert "read_file" in plan
    assert "list_files" in plan
    assert "web_search" in plan
    assert "current_time" in plan
    assert "task_create" in plan
    assert "task_update" in plan
    assert "task_get" in plan
    assert "task_list" in plan
    for forbidden in (
        "write_file",
        "shell",
        "http_request",
        "automation_create",
        "automation_pause",
        "memory_create",
        "memory_update",
        "memory_archive",
        "core_memory_update",
        "core_memory_remove",
        "skill_read",
        "tool_search",
    ):
        assert forbidden not in plan

    # 模型可见定义同样被过滤。
    plan_defs = {d.name for d in tools.model_definitions_for_mode(AgentMode.PLAN)}
    assert "write_file" not in plan_defs
    assert "read_file" in plan_defs


# ---------------------------------------------------------------------------
# plan mode 可以读 / search
# ---------------------------------------------------------------------------


async def test_plan_mode_allows_read_and_search(tmp_path) -> None:
    tools, task_store, stubs = await _build_tools(tmp_path)
    registry, _ = _registry(
        [
            _response(
                tool_calls=(
                    _call("read_file", {"path": "a.py"}),
                    _call("web_search", {"query": "computer runtime"}),
                )
            ),
            _response(content="计划说明"),
        ]
    )
    result = await _run(
        registry, tools, mode=AgentMode.PLAN, task_store=task_store
    )

    assert result.ok is True
    assert stubs["read_file"].executions == 1
    assert stubs["web_search"].executions == 1
    assert all(record.result.success for record in result.tool_calls)


# ---------------------------------------------------------------------------
# plan mode 可以 task_create → PENDING
# ---------------------------------------------------------------------------


async def test_plan_mode_creates_pending_task(tmp_path) -> None:
    tools, task_store, _ = await _build_tools(tmp_path)
    registry, _ = _registry(
        [
            _response(
                tool_calls=(
                    _call(
                        "task_create",
                        {
                            "title": "实现 Computer Runtime",
                            "goal": "实现 Computer Runtime V1",
                            "steps": [
                                {"title": "定义 protocol"},
                                {"title": "实现 observe"},
                                {"title": "实现 click/type"},
                            ],
                        },
                    ),
                )
            ),
            _response(content="计划已形成"),
        ]
    )
    result = await _run(
        registry, tools, mode=AgentMode.PLAN, task_store=task_store
    )

    assert result.ok is True
    assert result.plan_task_id is not None
    assert "without creating a task" not in (result.content or "")

    task = await task_store.get(result.plan_task_id)
    assert task is not None
    assert task.status is TaskStatus.PENDING  # 初始 PENDING
    assert task.goal == "实现 Computer Runtime V1"
    assert len(task.steps) == 3
    # 不要在尚未执行时伪造 DONE 步骤。
    assert all(step.status is TaskStepStatus.TODO for step in task.steps)


# ---------------------------------------------------------------------------
# plan mode 可以 task_update 计划内容
# ---------------------------------------------------------------------------


async def test_plan_mode_can_update_plan_content(tmp_path) -> None:
    tools, task_store, _ = await _build_tools(tmp_path)
    created = await task_store.create(
        title="初始计划",
        owner_conversation_id="conv-1",
    )
    registry, _ = _registry(
        [
            _response(
                tool_calls=(
                    _call(
                        "task_update",
                        {
                            "task_id": created.id,
                            "goal": "细化后的目标",
                            "constraints": ["不改动核心模块"],
                            "steps": [{"title": "第一步"}, {"title": "第二步"}],
                        },
                    ),
                )
            ),
            _response(content="计划已细化"),
        ]
    )
    result = await _run(
        registry, tools, mode=AgentMode.PLAN, task_store=task_store
    )

    assert result.ok is True
    assert result.plan_task_id == created.id
    updated = await task_store.get(created.id)
    assert updated is not None
    assert updated.goal == "细化后的目标"
    assert updated.constraints == ("不改动核心模块",)
    assert len(updated.steps) == 2
    assert updated.status is TaskStatus.PENDING



async def test_plan_mode_task_update_cannot_change_status(tmp_path) -> None:
    """Plan Mode 下 task_update 只能更新计划内容，不能改 status / 推进步骤。"""

    from app.models.types import ToolCall as TC
    from app.task.tools import TaskUpdateTool
    from app.tools.hooks import ToolExecutionContext

    tools, task_store, _ = await _build_tools(tmp_path)
    created = await task_store.create(
        title="初始计划",
        owner_conversation_id="conv-1",
    )
    update_tool: TaskUpdateTool = tools.get("task_update")

    status_args = {"task_id": created.id, "status": "active"}
    with pytest.raises(ValueError, match="改变任务状态"):
        await update_tool.execute_with_context(
            status_args,
            ToolExecutionContext(
                tool_call=TC(id="t1", name="task_update", arguments=status_args),
                run_id="run-1",
                conversation_id="conv-1",
                mode=AgentMode.PLAN,
            ),
        )
    assert (await task_store.get(created.id)).status is TaskStatus.PENDING


# ---------------------------------------------------------------------------
# plan mode 不能执行副作用工具（硬阻断）
# ---------------------------------------------------------------------------


async def test_plan_mode_blocks_side_effect_tools(tmp_path) -> None:
    tools, task_store, stubs = await _build_tools(tmp_path)
    registry, _ = _registry(
        [
            _response(
                tool_calls=(
                    _call("write_file", {"path": "evil.txt"}),
                    _call("shell", {"command": "rm -rf /"}),
                    _call("http_request", {"url": "https://example.com"}),
                )
            ),
            _response(content="我不应能执行副作用工具"),
        ]
    )
    result = await _run(
        registry, tools, mode=AgentMode.PLAN, task_store=task_store
    )

    # 工具调用被硬性阻断：全部失败，且 stub 从未真正执行。
    assert stubs["write_file"].executions == 0
    assert stubs["shell"].executions == 0
    assert stubs["http_request"].executions == 0
    for record in result.tool_calls:
        assert record.result.success is False
        assert "not allowed in plan mode" in (record.result.error or "")


# ---------------------------------------------------------------------------
# 未生成 Task 时返回明确提示
# ---------------------------------------------------------------------------


async def test_plan_mode_without_task_returns_clear_message(tmp_path) -> None:
    tools, task_store, _ = await _build_tools(tmp_path)
    registry, _ = _registry([_response(content="没有形成计划")])
    result = await _run(
        registry, tools, mode=AgentMode.PLAN, task_store=task_store
    )

    assert result.ok is True
    assert result.plan_task_id is None
    assert "Plan mode finished without creating a task" in (result.content or "")


# ---------------------------------------------------------------------------
# Plan Mode 最终 Task 校验（不能只凭 task_create/task_update 成功）
# ---------------------------------------------------------------------------


async def test_plan_mode_invalid_pending_task_is_not_success(tmp_path) -> None:
    """task_create 成功但缺 goal / steps → 不算有效计划。"""

    tools, task_store, _ = await _build_tools(tmp_path)
    registry, _ = _registry(
        [
            _response(
                tool_calls=(_call("task_create", {"title": "空计划"}),)
            ),
            _response(content="计划完成"),
        ]
    )
    result = await _run(
        registry, tools, mode=AgentMode.PLAN, task_store=task_store
    )

    assert result.ok is True
    assert result.plan_task_id is None  # 无效计划不暴露给 Desktop 展示
    assert "without a valid pending task" in (result.content or "")

    # 任务确实被创建了（仍是 PENDING），只是不够格当计划。
    tasks = await task_store.list()
    assert len(tasks) == 1
    assert tasks[0].status is TaskStatus.PENDING


async def test_plan_mode_valid_pending_task_passes(tmp_path) -> None:
    """goal + steps 齐全的 PENDING 计划正常通过。"""

    tools, task_store, _ = await _build_tools(tmp_path)
    registry, _ = _registry(
        [
            _response(
                tool_calls=(
                    _call(
                        "task_create",
                        {
                            "title": "实现 Computer Runtime",
                            "goal": "实现 Computer Runtime V1",
                            "steps": [
                                {"title": "定义 protocol"},
                                {"title": "实现 observe"},
                            ],
                        },
                    ),
                )
            ),
            _response(content="计划已形成"),
        ]
    )
    result = await _run(
        registry, tools, mode=AgentMode.PLAN, task_store=task_store
    )

    assert result.ok is True
    assert result.plan_task_id is not None
    assert "without a valid pending task" not in (result.content or "")
    task = await task_store.get(result.plan_task_id)
    assert task is not None and task.status is TaskStatus.PENDING


async def test_pending_plan_validation_conditions(tmp_path) -> None:
    """TaskContextProvider.pending_plan_is_valid 的完整条件。"""

    store = FileTaskStore(tmp_path / "tasks")
    await store.initialize()
    provider = TaskContextProvider(store)

    # 有效：PENDING + goal + 无 DONE/IN_PROGRESS 步骤。
    valid = await store.create(
        title="T",
        goal="G",
        steps=(TaskStep(id="s1", title="s1"),),
        owner_conversation_id="conv-1",
    )
    assert await provider.pending_plan_is_valid("conv-1", valid.id) is True

    # 缺 goal。
    no_goal = await store.create(
        title="T",
        steps=(TaskStep(id="s2", title="s2"),),
        owner_conversation_id="conv-1",
    )
    assert await provider.pending_plan_is_valid("conv-1", no_goal.id) is False

    # 缺 steps。
    no_steps = await store.create(
        title="T",
        goal="G",
        owner_conversation_id="conv-1",
    )
    assert await provider.pending_plan_is_valid("conv-1", no_steps.id) is False

    # 含 DONE / IN_PROGRESS 步骤 → 不算有效计划。
    with_done = await store.create(
        title="T",
        goal="G",
        steps=(
            TaskStep(
                id="s3",
                title="s3",
                status=TaskStepStatus.DONE,
                note="已完成",
            ),
        ),
        owner_conversation_id="conv-1",
    )
    assert await provider.pending_plan_is_valid("conv-1", with_done.id) is False
    with_progress = await store.create(
        title="T",
        goal="G",
        steps=(
            TaskStep(
                id="s4",
                title="s4",
                status=TaskStepStatus.IN_PROGRESS,
            ),
        ),
        owner_conversation_id="conv-1",
    )
    assert await provider.pending_plan_is_valid("conv-1", with_progress.id) is False

    # 非 PENDING（accept 后）→ 不算有效计划。
    await store.plan_accept(valid.id)
    assert await provider.pending_plan_is_valid("conv-1", valid.id) is False

    # 不属于该会话 / 不存在 → False。
    other = await store.create(
        title="T",
        goal="G",
        steps=(TaskStep(id="s5", title="s5"),),
        owner_conversation_id="conv-2",
    )
    assert await provider.pending_plan_is_valid("conv-1", other.id) is False
    assert await provider.pending_plan_is_valid("conv-1", "0" * 32) is False
    assert await provider.pending_plan_is_valid(None, "0" * 32) is False
    assert await provider.pending_plan_is_valid("conv-1", "") is False


# ---------------------------------------------------------------------------
# accept / reject / 非 PENDING 校验（store 层）
# ---------------------------------------------------------------------------


async def test_plan_accept_pending_to_active(tmp_path) -> None:
    store = FileTaskStore(tmp_path / "tasks")
    await store.initialize()
    task = await store.create(title="计划", owner_conversation_id="conv-1")
    assert task.status is TaskStatus.PENDING

    accepted = await store.plan_accept(task.id)
    assert accepted.status is TaskStatus.ACTIVE
    assert accepted.completed_at is None
    assert accepted.revision == task.revision + 1


async def test_plan_reject_pending_to_cancelled(tmp_path) -> None:
    store = FileTaskStore(tmp_path / "tasks")
    await store.initialize()
    task = await store.create(title="计划", owner_conversation_id="conv-1")

    rejected = await store.plan_reject(task.id)
    assert rejected.status is TaskStatus.CANCELLED
    assert rejected.completed_at is not None


async def test_non_pending_task_cannot_accept_or_reject(tmp_path) -> None:
    store = FileTaskStore(tmp_path / "tasks")
    await store.initialize()
    task = await store.create(title="计划", owner_conversation_id="conv-1")
    await store.plan_accept(task.id)  # ACTIVE

    with pytest.raises(ValueError, match="only pending"):
        await store.plan_accept(task.id)
    with pytest.raises(ValueError, match="only pending"):
        await store.plan_reject(task.id)

    # COMPLETED 同样不行。
    other = await store.create(title="另一计划", owner_conversation_id="conv-1")
    await store.set_status(other.id, TaskStatus.COMPLETED)
    with pytest.raises(ValueError, match="only pending"):
        await store.plan_accept(other.id)
    with pytest.raises(ValueError, match="only pending"):
        await store.plan_reject(other.id)


# ---------------------------------------------------------------------------
# ACTIVE 注入、PENDING 不注入
# ---------------------------------------------------------------------------


async def test_accept_then_injected_via_task_context(tmp_path) -> None:
    store = FileTaskStore(tmp_path / "tasks")
    await store.initialize()
    task = await store.create(title="计划", owner_conversation_id="conv-1")
    provider = TaskContextProvider(store)

    assert await provider.message_for("conv-1") is None  # PENDING 不注入

    await store.plan_accept(task.id)
    message = await provider.message_for("conv-1")
    assert message is not None and task.id in (message.content or "")


# ---------------------------------------------------------------------------
# Run 记录 mode（ConversationService → RunManager → store）
# ---------------------------------------------------------------------------


async def test_run_persists_mode(tmp_path) -> None:
    from app.checkpoint import SQLiteCheckpointStore
    from app.conversation.service import ConversationService
    from app.conversation.store import SQLiteConversationStore
    from app.run import RunManager, SQLiteRunStore
    from app.trace import SQLiteTraceStore

    database = tmp_path / "vesta.db"
    conversation_store = SQLiteConversationStore(database)
    await conversation_store.initialize()
    conversation = await conversation_store.create()
    trace_store = SQLiteTraceStore(database)
    await trace_store.initialize()
    run_store = SQLiteRunStore(database)
    await run_store.initialize()
    checkpoint_store = SQLiteCheckpointStore(database)
    await checkpoint_store.initialize()

    # normal（默认）
    tools, _, _ = await _build_tools(tmp_path)
    registry, _ = _registry([_response(content="普通回答")])
    runtime = AgentRuntime(registry, tools, provider="fake")
    run_manager = RunManager(run_store, checkpoint_store, runtime)
    service = ConversationService(conversation_store, run_manager, trace_store)
    dispatch = await service.dispatch(
        conversation_id=conversation.id,
        content="你好",
    )
    assert dispatch.run.mode is AgentMode.NORMAL

    # plan
    tools2, _, _ = await _build_tools(tmp_path)
    registry2, _ = _registry(
        [
            _response(tool_calls=(_call("task_create", {"title": "计划"}),)),
            _response(content="计划说明"),
        ]
    )
    runtime2 = AgentRuntime(registry2, tools2, provider="fake")
    run_manager2 = RunManager(run_store, checkpoint_store, runtime2)
    service2 = ConversationService(conversation_store, run_manager2, trace_store)
    dispatch2 = await service2.dispatch(
        conversation_id=conversation.id,
        content="规划一下",
        mode=AgentMode.PLAN,
    )
    assert dispatch2.run.mode is AgentMode.PLAN

    # 持久化后可重新读回。
    persisted = await run_store.get(dispatch2.run.id)
    assert persisted is not None
    assert persisted.mode is AgentMode.PLAN


# ---------------------------------------------------------------------------
# Plan Mode 仍产生正常 Trace / AgentEvent
# ---------------------------------------------------------------------------


async def test_plan_mode_emits_events(tmp_path) -> None:
    tools, task_store, _ = await _build_tools(tmp_path)
    registry, _ = _registry(
        [
            _response(tool_calls=(_call("read_file", {"path": "a.py"}),)),
            _response(tool_calls=(_call("task_create", {"title": "计划"}),)),
            _response(content="计划完成"),
        ]
    )
    handler = InMemoryEventHandler()
    result = await _run(
        registry,
        tools,
        mode=AgentMode.PLAN,
        task_store=task_store,
        handler=handler,
    )

    assert result.ok is True
    types = [event.type for event in handler.events]
    assert AgentEventType.AGENT_STARTED in types
    assert AgentEventType.AGENT_COMPLETED in types
    assert AgentEventType.TOOL_STARTED in types
    assert AgentEventType.TOOL_COMPLETED in types
    assert AgentEventType.MODEL_STARTED in types


# ---------------------------------------------------------------------------
# Automation / Normal Run 不受影响（mode 默认 normal）
# ---------------------------------------------------------------------------


async def test_automation_dispatch_defaults_to_normal(tmp_path) -> None:
    from app.checkpoint import SQLiteCheckpointStore
    from app.conversation.inputs import ConversationSource, TriggerContext
    from app.conversation.service import ConversationService
    from app.conversation.store import SQLiteConversationStore
    from app.run import RunManager, SQLiteRunStore
    from app.trace import SQLiteTraceStore

    database = tmp_path / "vesta.db"
    conversation_store = SQLiteConversationStore(database)
    await conversation_store.initialize()
    conversation = await conversation_store.create()
    trace_store = SQLiteTraceStore(database)
    await trace_store.initialize()
    run_store = SQLiteRunStore(database)
    await run_store.initialize()
    checkpoint_store = SQLiteCheckpointStore(database)
    await checkpoint_store.initialize()

    tools, _, _ = await _build_tools(tmp_path)
    registry, _ = _registry([_response(content="自动化回答")])
    runtime = AgentRuntime(registry, tools, provider="fake")
    run_manager = RunManager(run_store, checkpoint_store, runtime)
    service = ConversationService(conversation_store, run_manager, trace_store)

    dispatch = await service.dispatch(
        conversation_id=conversation.id,
        content="定时任务",
        trigger=TriggerContext(
            source=ConversationSource.AUTOMATION,
            automation_id="auto-1",
        ),
    )
    # Automation 不传 mode → 默认 normal。
    assert dispatch.run.mode is AgentMode.NORMAL
    assert dispatch.run.source == "automation"
    assert dispatch.run.source_id == "auto-1"
