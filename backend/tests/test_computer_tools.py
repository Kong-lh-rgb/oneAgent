"""Computer Tools V0 测试。

全部使用 FakeComputerRuntime，不调用真实 macOS API、不需要 Accessibility /
Screen Recording 权限、不调用模型 API（fake model）。

覆盖（对应需求 14）：
1-8. 7 个工具的调用与参数解析
9. action_history 顺序
10. 无 computer_runtime 时不注册 computer tools
11. 注入 FakeComputerRuntime 后正确注册
12-14. Approval：click 经过现有审批 / approve 执行 / deny 不执行
15. computer tools 产生现有 Trace / AgentEvent
16. PLAN Mode 看不到 / 不能执行 computer tools
17. 现有其它 Tool 测试不受影响（完整套件验证）
+ AgentRuntime 集成测试：observe → click → final answer
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import pytest
from pydantic import SecretStr

from app.agent.events import AgentEventType, InMemoryEventHandler
from app.agent.runtime import AgentRuntime
from app.computer import (
    ActionName,
    ActionResult,
    ComputerClickTool,
    ComputerFocusWindowTool,
    ComputerKeyTool,
    ComputerObserveTool,
    ComputerOpenAppTool,
    ComputerScrollTool,
    ComputerTypeTool,
    DeliveryStatus,
    Element,
    FakeComputerRuntime,
    Observation,
    VerificationStatus,
    default_observation,
    register_computer_tools,
)
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
    ToolPermission,
)
from app.tools import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalRequest,
    ApprovalResponse,
    AutoApproveGate,
    ToolExecutor,
    ToolRegistry,
)


class RecordingGate(ApprovalGate):
    """记录审批请求并按预设决定返回。"""

    def __init__(self, decision: ApprovalDecision) -> None:
        self.decision = decision
        self.requests: list[ApprovalRequest] = []

    async def request_approval(self, request: ApprovalRequest) -> ApprovalResponse:
        self.requests.append(request)
        return ApprovalResponse(decision=self.decision)


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


def _model_response(
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


def _fake_registry(
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


def _build(observation=None) -> tuple[ToolRegistry, FakeComputerRuntime]:
    """构造注册了全部 7 个 computer_* 工具的 registry + fake runtime。"""

    fake = FakeComputerRuntime(observation=observation)
    registry = ToolRegistry()
    register_computer_tools(registry, fake)
    return registry, fake


def _executor(
    registry: ToolRegistry,
    gate: ApprovalGate | None = None,
) -> ToolExecutor:
    return ToolExecutor(registry, approval_gate=gate or AutoApproveGate())


_COMPUTER_TOOLS = (
    "computer_observe",
    "computer_click",
    "computer_type",
    "computer_key",
    "computer_scroll",
    "computer_open_app",
    "computer_focus_window",
)


# ---------------------------------------------------------------------------
# 1-8. 各工具调用与参数解析
# ---------------------------------------------------------------------------


async def test_computer_observe_calls_runtime() -> None:
    obs = default_observation()
    registry, _ = _build(observation=obs)
    result = await _executor(registry).execute(
        ToolCall(id="o1", name="computer_observe", arguments={})
    )
    assert result.success is True
    payload = json.loads(result.output or "{}")
    assert payload["id"] == obs.id
    assert payload["active_app"]["name"] == "FakeApp"


async def test_computer_observe_never_returns_truncated_json() -> None:
    observation = Observation(
        id="large-observation",
        focused_element_ref="editor",
        elements=(
            Element(
                ref="editor",
                role="text_area",
                value="正在编辑",
                focused=True,
                editable=True,
            ),
            *(
                Element(
                    ref=f"cell-{index}",
                    role="cell",
                    title="侧边栏" + "x" * 900,
                )
                for index in range(300)
            ),
        ),
    )
    registry, _ = _build(observation=observation)

    result = await _executor(registry).execute(
        ToolCall(id="large", name="computer_observe", arguments={})
    )

    payload = json.loads(result.output or "{}")
    assert result.success is True
    assert len(result.output or "") < 20_000
    assert payload["elements"][0]["ref"] == "editor"
    assert payload["element_stats"]["returned"] < 301
    assert payload["truncated"] is True


async def test_last_step_observation_gets_tool_free_finalization() -> None:
    registry, adapter = _fake_registry(
        [
            _model_response(
                tool_calls=(
                    ToolCall(
                        id="observe-final",
                        name="computer_observe",
                        arguments={"include_screenshot": False},
                    ),
                )
            ),
            _model_response(content="已读取最后一次观察；输入效果尚未确认。"),
        ]
    )
    tools, _ = _build()

    result = await AgentRuntime(
        registry,
        tools,
        provider="fake",
        max_steps=1,
    ).run("检查界面")

    assert result.ok is True
    assert result.steps == 2
    assert len(adapter.requests) == 2
    assert adapter.requests[-1].tools == ()
    assert "尚未确认" in (result.content or "")


async def test_textual_tool_call_in_finalization_is_not_completed() -> None:
    registry, adapter = _fake_registry(
        [
            _model_response(
                tool_calls=(
                    ToolCall(
                        id="observe-final",
                        name="computer_observe",
                        arguments={"include_screenshot": False},
                    ),
                )
            ),
            _model_response(
                content=(
                    '<｜｜DSML｜｜tool_calls><｜｜DSML｜｜invoke '
                    'name="computer_type">123'
                )
            ),
        ]
    )
    tools, _ = _build()

    result = await AgentRuntime(
        registry,
        tools,
        provider="fake",
        max_steps=1,
    ).run("输入 123")

    assert result.ok is False
    assert result.stop_reason.value == "max_steps"
    assert len(adapter.requests) == 2


async def test_unverified_type_cannot_be_reported_as_completed() -> None:
    class UnverifiedComputer(FakeComputerRuntime):
        async def type(
            self,
            text: str,
            element_ref: str | None = None,
        ) -> ActionResult:
            return ActionResult(
                success=True,
                action=ActionName.TYPE,
                delivery_status=DeliveryStatus.DELIVERED,
                verification_status=VerificationStatus.UNVERIFIED,
                metadata={"characters": len(text), "element_ref": element_ref},
            )

    registry, adapter = _fake_registry(
        [
            _model_response(
                tool_calls=(
                    ToolCall(
                        id="type-1",
                        name="computer_type",
                        arguments={"text": "465", "element_ref": "e2"},
                    ),
                )
            ),
            _model_response(content="已经输入成功"),
            _model_response(
                tool_calls=(
                    ToolCall(
                        id="observe-1",
                        name="computer_observe",
                        arguments={"include_screenshot": False},
                    ),
                )
            ),
            _model_response(content="事件已投递，并已重新观察界面。"),
        ]
    )
    tools = ToolRegistry()
    register_computer_tools(tools, UnverifiedComputer())

    result = await AgentRuntime(
        registry,
        tools,
        provider="fake",
        max_steps=4,
        approval_gate=AutoApproveGate(),
    ).run("输入 465")

    assert result.ok is True
    assert result.steps == 4
    assert len(adapter.requests) == 4
    assert any(
        message.role is MessageRole.SYSTEM
        and "尚未确认界面效果" in (message.content or "")
        for message in adapter.requests[2].messages
    )


async def test_computer_click_element_target_parses() -> None:
    registry, fake = _build()
    tool = ComputerClickTool(fake)
    result = await tool.execute(
        {"observation_id": "obs-1", "element_ref": "e1"}
    )
    assert result["success"] is True
    assert result["action"] == "click"
    assert fake.action_history[0].metadata["element_ref"] == "e1"
    assert "x" not in fake.action_history[0].metadata


async def test_computer_click_coordinate_target_parses() -> None:
    registry, fake = _build()
    tool = ComputerClickTool(fake)
    result = await tool.execute(
        {"observation_id": "obs-1", "x": 100, "y": 200}
    )
    assert result["success"] is True
    assert result["action"] == "click"
    assert fake.action_history[0].metadata["x"] == 100
    assert fake.action_history[0].metadata["y"] == 200
    assert "element_ref" not in fake.action_history[0].metadata


async def test_computer_click_rejects_ambiguous_target() -> None:
    _, fake = _build()
    tool = ComputerClickTool(fake)
    # element_ref 与 x/y 同时给出 → 拒绝。
    with pytest.raises(ValueError, match="not both"):
        await tool.execute(
            {"observation_id": "obs-1", "element_ref": "e1", "x": 1, "y": 2}
        )
    # 缺 observation_id / 两者都不给 → 拒绝。
    with pytest.raises(ValueError, match="observation_id"):
        await tool.execute({"element_ref": "e1"})
    with pytest.raises(ValueError, match="either element_ref or both"):
        await tool.execute({"observation_id": "obs-1"})


async def test_computer_type_calls_runtime() -> None:
    _, fake = _build()
    tool = ComputerTypeTool(fake)
    result = await tool.execute({"text": "hello"})
    assert result["action"] == "type"
    assert fake.action_history[0].metadata["text"] == "hello"


async def test_computer_type_with_element_ref() -> None:
    _, fake = _build()
    tool = ComputerTypeTool(fake)
    result = await tool.execute({"text": "hello", "element_ref": "e2"})
    assert result["action"] == "type"
    assert fake.action_history[0].metadata["text"] == "hello"
    assert fake.action_history[0].metadata["element_ref"] == "e2"


async def test_computer_type_element_ref_validation() -> None:
    _, fake = _build()
    tool = ComputerTypeTool(fake)
    with pytest.raises(ValueError, match="element_ref"):
        await tool.execute({"text": "hi", "element_ref": ""})
    with pytest.raises(ValueError, match="element_ref"):
        await tool.execute({"text": "hi", "element_ref": 123})
    assert fake.action_history == []


async def test_computer_key_with_modifiers() -> None:
    _, fake = _build()
    tool = ComputerKeyTool(fake)
    result = await tool.execute(
        {"key": "c", "modifiers": ["command", "shift"]}
    )
    assert result["action"] == "key"
    assert fake.action_history[0].metadata["key"] == "c"
    assert fake.action_history[0].metadata["modifiers"] == ("command", "shift")

    # 缺省 modifiers → 空。
    await tool.execute({"key": "enter"})
    assert fake.action_history[1].metadata["modifiers"] == ()


async def test_computer_key_with_element_ref() -> None:
    _, fake = _build()
    tool = ComputerKeyTool(fake)
    result = await tool.execute(
        {"key": "enter", "modifiers": ["command"], "element_ref": "e2"}
    )
    assert result["action"] == "key"
    assert fake.action_history[0].metadata["key"] == "enter"
    assert fake.action_history[0].metadata["modifiers"] == ("command",)
    assert fake.action_history[0].metadata["element_ref"] == "e2"


async def test_computer_key_element_ref_validation() -> None:
    _, fake = _build()
    tool = ComputerKeyTool(fake)
    with pytest.raises(ValueError, match="element_ref"):
        await tool.execute({"key": "a", "element_ref": " "})
    assert fake.action_history == []


async def test_computer_scroll_calls_runtime() -> None:
    _, fake = _build()
    tool = ComputerScrollTool(fake)
    result = await tool.execute({"delta_x": 0, "delta_y": -3})
    assert result["action"] == "scroll"
    assert fake.action_history[0].metadata["delta_y"] == -3

    # 至少一个非 0。
    with pytest.raises(ValueError, match="non-zero"):
        await tool.execute({"delta_x": 0, "delta_y": 0})


async def test_computer_open_app_calls_runtime() -> None:
    _, fake = _build()
    tool = ComputerOpenAppTool(fake)
    result = await tool.execute({"app": "Notes"})
    assert result["action"] == "open_app"
    assert fake.action_history[0].metadata["app"] == "Notes"

    with pytest.raises(ValueError, match="non-empty"):
        await tool.execute({"app": "   "})


async def test_computer_focus_window_calls_runtime() -> None:
    _, fake = _build()
    tool = ComputerFocusWindowTool(fake)
    result = await tool.execute({"window_ref": "w1"})
    assert result["action"] == "focus_window"
    assert fake.action_history[0].metadata["window_ref"] == "w1"

    with pytest.raises(ValueError, match="non-empty"):
        await tool.execute({"window_ref": ""})


# ---------------------------------------------------------------------------
# 9. action_history 顺序
# ---------------------------------------------------------------------------


async def test_action_history_order() -> None:
    _, fake = _build()
    obs = fake.observation

    await ComputerOpenAppTool(fake).execute({"app": "Notes"})
    await ComputerObserveTool(fake).execute({})
    await ComputerClickTool(fake).execute(
        {"observation_id": obs.id, "element_ref": "e1"}
    )
    await ComputerTypeTool(fake).execute({"text": "hi"})
    await ComputerKeyTool(fake).execute({"key": "enter"})
    await ComputerScrollTool(fake).execute({"delta_y": 2})
    await ComputerFocusWindowTool(fake).execute({"window_ref": "w2"})

    from app.computer import ActionName

    assert [item.action for item in fake.action_history] == [
        ActionName.OPEN_APP,
        ActionName.CLICK,  # observe 返回 Observation，不进入 action_history
        ActionName.TYPE,
        ActionName.KEY,
        ActionName.SCROLL,
        ActionName.FOCUS_WINDOW,
    ]


# ---------------------------------------------------------------------------
# 10-11. 注册：无 runtime 不注册；注入后注册
# ---------------------------------------------------------------------------


async def test_register_computer_tools_registers_all(tmp_path) -> None:
    registry, _ = _build()
    names = registry.names()
    for name in _COMPUTER_TOOLS:
        assert name in names


async def _build_application(tmp_path, *, computer_runtime):
    from app.application import Application
    from app.memory import MemoryMaintenanceConfig, MemoryReflectionConfig
    from app.skill_learning import SkillLearningSettings

    config = ProviderConfig(
        provider="fake",
        model="fake-model",
        api_key=SecretStr("offline-test-key"),
        api_style=ApiStyle.CHAT_COMPLETIONS,
    )
    adapter = ScriptedAdapter(config, [_model_response(content="ok")])
    model_registry = ModelAdapterRegistry(ModelSettings(_env_file=None))
    model_registry.register("fake", lambda _: adapter, config=config)

    application = Application(
        provider="fake",
        model="fake-model",
        database=tmp_path / "vesta.db",
        tasks_dir=tmp_path / "tasks",
        mcp_config=tmp_path / "mcp.json",
        memory_dir=tmp_path / "memory",
        skills_user_dir=tmp_path / "skills-user",
        skills_project_dir=tmp_path / "skills-project",
        registry=model_registry,
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
        computer_runtime=computer_runtime,
    )
    await application.start()
    return application


async def test_no_computer_runtime_skips_registration(tmp_path) -> None:
    app = await _build_application(tmp_path, computer_runtime=None)
    try:
        registry = app.tool_registry
        assert registry is not None
        for name in _COMPUTER_TOOLS:
            assert name not in registry.names()
        assert app.computer_runtime is None
    finally:
        await app.close()


async def test_fake_computer_runtime_registers_tools(tmp_path) -> None:
    fake = FakeComputerRuntime()
    app = await _build_application(tmp_path, computer_runtime=fake)
    try:
        registry = app.tool_registry
        assert registry is not None
        for name in _COMPUTER_TOOLS:
            assert name in registry.names()
        assert app.computer_runtime is fake
    finally:
        await app.close()


# ---------------------------------------------------------------------------
# 12-14. Approval 复用现有链路
# ---------------------------------------------------------------------------


async def test_click_permission_is_human_approval() -> None:
    fake = FakeComputerRuntime()
    assert (
        ComputerClickTool(fake).definition.permission
        is ToolPermission.HUMAN_APPROVAL
    )
    assert (
        ComputerTypeTool(fake).definition.permission
        is ToolPermission.HUMAN_APPROVAL
    )
    assert (
        ComputerKeyTool(fake).definition.permission
        is ToolPermission.HUMAN_APPROVAL
    )
    assert (
        ComputerObserveTool(fake).definition.permission
        is ToolPermission.ALLOWED
    )
    assert (
        ComputerScrollTool(fake).definition.permission
        is ToolPermission.ALLOWED
    )
    assert (
        ComputerOpenAppTool(fake).definition.permission
        is ToolPermission.ALLOWED
    )
    assert (
        ComputerFocusWindowTool(fake).definition.permission
        is ToolPermission.ALLOWED
    )


async def test_click_goes_through_existing_approval() -> None:
    registry, fake = _build()
    gate = RecordingGate(ApprovalDecision.APPROVED)
    executor = _executor(registry, gate)

    result = await executor.execute(
        ToolCall(
            id="c1",
            name="computer_click",
            arguments={"observation_id": "obs-1", "element_ref": "e1"},
        )
    )

    assert result.success is True
    assert len(gate.requests) == 1
    assert gate.requests[0].tool_name == "computer_click"
    assert gate.requests[0].arguments == {
        "observation_id": "obs-1",
        "element_ref": "e1",
    }


async def test_click_approved_executes_fake_runtime() -> None:
    registry, fake = _build()
    executor = _executor(registry, AutoApproveGate())

    result = await executor.execute(
        ToolCall(
            id="c1",
            name="computer_click",
            arguments={"observation_id": "obs-1", "element_ref": "e1"},
        )
    )

    assert result.success is True
    assert len(fake.action_history) == 1
    assert fake.action_history[0].action.value == "click"


async def test_click_denied_does_not_execute_fake_runtime() -> None:
    registry, fake = _build()
    executor = _executor(registry, RecordingGate(ApprovalDecision.DENIED))

    result = await executor.execute(
        ToolCall(
            id="c1",
            name="computer_click",
            arguments={"observation_id": "obs-1", "element_ref": "e1"},
        )
    )

    assert result.success is False
    assert fake.action_history == []


# ---------------------------------------------------------------------------
# 15. Trace / AgentEvent + AgentRuntime 集成
# ---------------------------------------------------------------------------


async def test_agent_runtime_computer_chain() -> None:
    """Fake model：observe → click → final answer，验证完整链。"""

    registry, _ = _fake_registry(
        [
            _model_response(
                tool_calls=(
                    ToolCall(id="o1", name="computer_observe", arguments={}),
                )
            ),
            _model_response(
                tool_calls=(
                    ToolCall(
                        id="c1",
                        name="computer_click",
                        arguments={
                            "observation_id": "obs-1",
                            "element_ref": "e1",
                        },
                    ),
                )
            ),
            _model_response(content="已完成"),
        ]
    )
    tools, fake = _build()
    handler = InMemoryEventHandler()
    runtime = AgentRuntime(
        registry,
        tools,
        provider="fake",
        approval_gate=AutoApproveGate(),
    )
    result = await runtime.run(
        "帮我操作电脑",
        conversation_id="conv-1",
        run_id="run-1",
        event_handler=handler,
    )

    assert result.ok is True
    # AgentRuntime → ToolRegistry → Computer Tool → FakeComputerRuntime → ToolResult
    assert result.tool_calls[0].tool_call.name == "computer_observe"
    assert result.tool_calls[0].result.success is True
    assert result.tool_calls[1].tool_call.name == "computer_click"
    assert result.tool_calls[1].result.success is True
    # 实际进入 FakeComputerRuntime.action_history。
    assert [item.action.value for item in fake.action_history] == [
        "click",
    ]

    # 现有 Trace / AgentEvent：TOOL_STARTED / TOOL_COMPLETED / 审批事件。
    types = [event.type for event in handler.events]
    assert AgentEventType.TOOL_STARTED in types
    assert AgentEventType.TOOL_COMPLETED in types
    assert AgentEventType.TOOL_APPROVAL_REQUIRED in types
    assert AgentEventType.TOOL_APPROVAL_COMPLETED in types
    assert AgentEventType.AGENT_COMPLETED in types


# ---------------------------------------------------------------------------
# 16. PLAN Mode 看不到 / 不能执行 computer tools
# ---------------------------------------------------------------------------


async def test_plan_mode_hides_and_blocks_computer_tools() -> None:
    tools, fake = _build()

    # 模型定义隐藏（PLAN 白名单不含 computer_*）。
    plan_defs = {
        definition.name
        for definition in tools.model_definitions_for_mode(AgentMode.PLAN)
    }
    for name in _COMPUTER_TOOLS:
        assert name not in plan_defs
        assert name not in tools.allowed_names_for_mode(AgentMode.PLAN)

    # 执行层硬阻断。
    registry, _ = _fake_registry(
        [
            _model_response(
                tool_calls=(
                    ToolCall(
                        id="c1",
                        name="computer_click",
                        arguments={
                            "observation_id": "obs-1",
                            "element_ref": "e1",
                        },
                    ),
                )
            ),
            _model_response(content="不应执行"),
        ]
    )
    runtime = AgentRuntime(
        registry,
        tools,
        provider="fake",
        approval_gate=AutoApproveGate(),
    )
    result = await runtime.run(
        "规划",
        conversation_id="conv-1",
        run_id="run-1",
        mode=AgentMode.PLAN,
    )
    assert result.tool_calls[0].result.success is False
    assert "not allowed in plan mode" in (result.tool_calls[0].result.error or "")
    assert fake.action_history == []
