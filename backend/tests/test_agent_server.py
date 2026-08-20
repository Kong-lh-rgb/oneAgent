"""Vesta Host 测试（全部用离线 fake model，不调用真实模型 API）。

覆盖：WebSocket JSON-RPC 协议（request/response correlation / parse error /
invalid request / method not found / invalid params / internal error /
notification）/ conversation CRUD / conversation.send 走 ConversationService 并
实时收到 agent.event / 长请求不阻塞同 socket 的 run.cancel / run list·get·
recover / trace.get / automation CRUD·control / Automation Run 同 socket 广播 +
provenance=automation / Desktop 断开不取消 Run / Application start·close 幂等 /
shutdown 正确关闭资源。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.application import Application
from app.memory import (
    MemoryMaintenanceConfig,
    MemoryReflectionConfig,
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
    ToolDefinition,
    ToolPermission,
)
from app.server.app import create_app
from app.skill_learning import SkillLearningSettings
from app.tools.base import BaseTool


def _model_response(
    content: str = "已完成",
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
        usage=ModelUsage(input_tokens=10, output_tokens=5, total_tokens=15),
    )


class RepeatingFakeAdapter(ModelAdapter):
    """每次调用都返回同一个响应的离线适配器。"""

    def __init__(self, config: ProviderConfig, response: ModelResponse) -> None:
        super().__init__(config)
        self.response = response
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return self.response

    async def close(self) -> None:
        pass


class ScriptedFakeAdapter(ModelAdapter):
    """按脚本顺序返回响应的离线适配器（审批 / 多步测试用）。"""

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


class ApprovalProbeTool(BaseTool):
    """HUMAN_APPROVAL 档位的探测工具（注册进运行中的 application）。"""

    definition = ToolDefinition(
        name="approval_probe",
        description="需要审批的探测工具",
        parameters={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
        },
        permission=ToolPermission.HUMAN_APPROVAL,
    )

    def __init__(self) -> None:
        self.executions = 0

    async def execute(self, arguments: dict[str, object]) -> str:
        self.executions += 1
        return f"probe-{arguments.get('value')}"


def _drain_until(websocket: Any, predicate: Any, *, limit: int = 500) -> dict[str, Any]:
    """读取消息直到 predicate 命中并返回；中间消息丢弃。"""

    for _ in range(limit):
        message = json.loads(websocket.receive_text())
        if predicate(message):
            return message
    raise AssertionError("未在限次内等到目标消息")


def _model_with_tool_call() -> ModelResponse:
    return _model_response(
        tool_calls=(
            ToolCall(
                id="ap-1",
                name="approval_probe",
                arguments={"value": 7},
            ),
        )
    )


class BlockingFakeAdapter(ModelAdapter):
    """阻塞在模型请求上，直到被取消（用于 cancel / disconnect 测试）。"""

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self.started = asyncio.Event()
        self.cancelled = False
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        raise AssertionError("阻塞模型不应正常完成")

    async def close(self) -> None:
        pass


@pytest.fixture
def make_app(tmp_path):
    """构造一个可注入 fake registry 的 Application + Host app。"""

    def build(
        *,
        blocking: bool = False,
        response_text: str = "已完成",
        responses: Sequence[ModelResponse | Exception] | None = None,
    ):
        config = ProviderConfig(
            provider="fake",
            model="fake-model",
            api_key=SecretStr("offline-test-key"),
            api_style=ApiStyle.CHAT_COMPLETIONS,
        )
        if blocking:
            adapter: ModelAdapter = BlockingFakeAdapter(config)
        elif responses is not None:
            adapter = ScriptedFakeAdapter(config, responses)
        else:
            adapter = RepeatingFakeAdapter(config, _model_response(response_text))
        registry = ModelAdapterRegistry(ModelSettings(_env_file=None))
        registry.register("fake", lambda _: adapter, config=config)

        application = Application(
            provider="fake",
            model="fake-model",
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
        app = create_app(application)
        return app, application, adapter

    return build


def _rpc_request(
    request_id: int,
    method: str,
    params: dict[str, Any] | None = None,
) -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        },
        ensure_ascii=False,
    )


def _rpc_call(
    websocket: Any,
    request_id: int,
    method: str,
    params: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """发送请求并接收直到匹配 id 的响应；期间的消息视为 notification。"""

    websocket.send_text(_rpc_request(request_id, method, params))
    notifications: list[dict[str, Any]] = []
    while True:
        message = json.loads(websocket.receive_text())
        if message.get("id") == request_id:
            return message, notifications
        notifications.append(message)


def _require_result(response: dict[str, Any]) -> dict[str, Any]:
    assert "error" not in response, f"unexpected error: {response.get('error')}"
    return response["result"]


# ---------------------------------------------------------------------------
# 协议：parse / invalid / correlation / notification
# ---------------------------------------------------------------------------


def test_parse_error(make_app) -> None:
    app, _, _ = make_app()
    with TestClient(app) as client:
        with client.websocket_connect("/rpc") as websocket:
            websocket.send_text("{not valid json")
            message = json.loads(websocket.receive_text())
            assert message["id"] is None
            assert message["error"]["code"] == -32700


def test_invalid_request_missing_method(make_app) -> None:
    app, _, _ = make_app()
    with TestClient(app) as client:
        with client.websocket_connect("/rpc") as websocket:
            websocket.send_text(json.dumps({"jsonrpc": "2.0", "id": 1}))
            message = json.loads(websocket.receive_text())
            assert message["id"] == 1
            assert message["error"]["code"] == -32600


def test_method_not_found(make_app) -> None:
    app, _, _ = make_app()
    with TestClient(app) as client:
        with client.websocket_connect("/rpc") as websocket:
            message, _ = _rpc_call(websocket, 1, "no.such.method")
            assert message["id"] == 1
            assert message["error"]["code"] == -32601


def test_invalid_params(make_app) -> None:
    app, _, _ = make_app()
    with TestClient(app) as client:
        with client.websocket_connect("/rpc") as websocket:
            # conversation.create 的 title 必须是字符串。
            message, _ = _rpc_call(
                websocket,
                1,
                "conversation.create",
                {"title": 123},
            )
            assert message["id"] == 1
            assert message["error"]["code"] == -32602


def test_internal_error_does_not_leak_traceback(make_app) -> None:
    app, _, _ = make_app()
    with TestClient(app) as client:
        # 注册一个必然抛异常的 method，验证返回通用 Internal error（无 traceback）。
        async def explode(params: dict[str, Any], ctx: Any) -> None:
            raise RuntimeError("secret inner detail")

        app.state.dispatcher.register("test.explode", explode)
        with client.websocket_connect("/rpc") as websocket:
            message, _ = _rpc_call(websocket, 1, "test.explode")
            assert message["error"]["code"] == -32603
            assert message["error"]["message"] == "Internal error"
            assert "secret inner detail" not in json.dumps(message)


def test_request_id_correlation(make_app) -> None:
    app, _, _ = make_app()
    with TestClient(app) as client:
        with client.websocket_connect("/rpc") as websocket:
            websocket.send_text(_rpc_request(10, "system.info"))
            websocket.send_text(_rpc_request(20, "system.info"))
            first = json.loads(websocket.receive_text())
            second = json.loads(websocket.receive_text())
            assert {first.get("id"), second.get("id")} == {10, 20}
            assert first["result"]["provider"] == "fake"
            assert second["result"]["provider"] == "fake"


def test_notification_gets_no_response(make_app) -> None:
    app, _, _ = make_app()
    with TestClient(app) as client:
        with client.websocket_connect("/rpc") as websocket:
            # 通知（无 id）：不应产生任何响应。
            websocket.send_text(
                json.dumps({"jsonrpc": "2.0", "method": "conversation.list"})
            )
            # 紧接着一个正常请求：只应收到它的响应。
            message, _ = _rpc_call(websocket, 7, "system.info")
            assert message["id"] == 7
            assert "result" in message


def test_system_info(make_app) -> None:
    app, _, _ = make_app()
    with TestClient(app) as client:
        with client.websocket_connect("/rpc") as websocket:
            message, _ = _rpc_call(websocket, 1, "system.info")
            result = _require_result(message)
            assert result["status"] == "ok"
            assert result["provider"] == "fake"
            assert result["model"] == "fake-model"
            assert result["database"]


# ---------------------------------------------------------------------------
# conversation
# ---------------------------------------------------------------------------


def test_conversation_create_list_get(make_app) -> None:
    app, _, _ = make_app()
    with TestClient(app) as client:
        with client.websocket_connect("/rpc") as websocket:
            created = _require_result(
                _rpc_call(websocket, 1, "conversation.create")[0]
            )
            conversation_id = created["conversation"]["id"]

            listed = _require_result(_rpc_call(websocket, 2, "conversation.list")[0])
            assert any(
                item["id"] == conversation_id for item in listed["conversations"]
            )

            detail = _require_result(
                _rpc_call(
                    websocket,
                    3,
                    "conversation.get",
                    {"conversation_id": conversation_id},
                )[0]
            )
            assert detail["conversation"]["id"] == conversation_id
            assert detail["messages"] == []


def test_conversation_send_goes_through_service_and_writes_back(make_app) -> None:
    app, application, _ = make_app()
    with TestClient(app) as client:
        with client.websocket_connect("/rpc") as websocket:
            conversation_id = _require_result(
                _rpc_call(websocket, 1, "conversation.create")[0]
            )["conversation"]["id"]

            # 记录 dispatch 是否被调用（证明走 ConversationService 统一入口）。
            original_dispatch = application.conversation_service.dispatch
            calls: list[dict[str, Any]] = []

            async def spy_dispatch(**kwargs: Any):
                calls.append(kwargs)
                return await original_dispatch(**kwargs)

            application.conversation_service.dispatch = spy_dispatch  # type: ignore[method-assign]

            message, notifications = _rpc_call(
                websocket,
                2,
                "conversation.send",
                {"conversation_id": conversation_id, "content": "帮我总结进度"},
            )
            result = _require_result(message)
            assert result["conversation_id"] == conversation_id
            assert result["run"]["conversation_id"] == conversation_id
            assert result["content"] == "已完成"
            assert calls and calls[0]["conversation_id"] == conversation_id

            # 执行期间收到 agent.event notification。
            agent_types = {
                item["params"]["type"]
                for item in notifications
                if item.get("method") == "agent.event"
            }
            assert "agent_started" in agent_types
            assert "agent_completed" in agent_types

            # 标题由首条消息生成。
            detail = _require_result(
                _rpc_call(
                    websocket,
                    3,
                    "conversation.get",
                    {"conversation_id": conversation_id},
                )[0]
            )
            assert detail["conversation"]["title"] == "帮我总结进度"
            roles = [msg["role"] for msg in detail["messages"]]
            assert "user" in roles and "assistant" in roles


def test_conversation_send_missing_conversation(make_app) -> None:
    app, _, _ = make_app()
    with TestClient(app) as client:
        with client.websocket_connect("/rpc") as websocket:
            message, _ = _rpc_call(
                websocket,
                1,
                "conversation.send",
                {"conversation_id": "nope", "content": "hi"},
            )
            assert message["error"]["code"] == -32000


# ---------------------------------------------------------------------------
# 长请求不阻塞：send 期间可 run.cancel
# ---------------------------------------------------------------------------


def test_cancel_while_send_in_flight(make_app) -> None:
    app, application, adapter = make_app(blocking=True)
    with TestClient(app) as client:
        with client.websocket_connect("/rpc") as websocket:
            conversation_id = _require_result(
                _rpc_call(websocket, 1, "conversation.create")[0]
            )["conversation"]["id"]

            # 发送长请求（阻塞模型），不等待响应。
            websocket.send_text(
                _rpc_request(
                    2,
                    "conversation.send",
                    {"conversation_id": conversation_id, "content": "阻塞"},
                )
            )
            for _ in range(200):
                if adapter.started.is_set():
                    break
                time.sleep(0.01)
            assert adapter.started.is_set()

            # 从 agent_started notification 拿到 run_id。
            run_id = None
            for _ in range(50):
                msg = json.loads(websocket.receive_text())
                if (
                    msg.get("method") == "agent.event"
                    and msg["params"].get("type") == "agent_started"
                ):
                    run_id = msg["params"]["run_id"]
                    break
            assert run_id is not None

            # send 尚未完成时，同一 socket 发送 run.cancel。
            message, _ = _rpc_call(websocket, 3, "run.cancel", {"run_id": run_id})
            result = _require_result(message)
            assert result["run"]["status"] == "cancelled"
            # 注：send(id=2) 的错误响应可能在 cancel 响应前后到达，已被
            # _rpc_call 作为 notification 消费或留在队列里，无需额外 drain。


# ---------------------------------------------------------------------------
# run list / get / recover / trace
# ---------------------------------------------------------------------------


def test_run_list_and_detail(make_app) -> None:
    app, _, _ = make_app()
    with TestClient(app) as client:
        with client.websocket_connect("/rpc") as websocket:
            conversation_id = _require_result(
                _rpc_call(websocket, 1, "conversation.create")[0]
            )["conversation"]["id"]
            run_id = _require_result(
                _rpc_call(
                    websocket,
                    2,
                    "conversation.send",
                    {"conversation_id": conversation_id, "content": "运行一次"},
                )[0]
            )["run"]["id"]

            listed = _require_result(_rpc_call(websocket, 3, "run.list")[0])
            assert any(item["id"] == run_id for item in listed["runs"])

            filtered = _require_result(
                _rpc_call(
                    websocket,
                    4,
                    "run.list",
                    {"conversation_id": conversation_id},
                )[0]
            )
            assert any(item["id"] == run_id for item in filtered["runs"])

            detail = _require_result(
                _rpc_call(websocket, 5, "run.get", {"run_id": run_id})[0]
            )["run"]
            assert detail["status"] == "completed"
            assert detail["source"] == "manual"
            assert detail["conversation_id"] == conversation_id


def test_run_cancel_terminal_conflict(make_app) -> None:
    app, _, _ = make_app()
    with TestClient(app) as client:
        with client.websocket_connect("/rpc") as websocket:
            conversation_id = _require_result(
                _rpc_call(websocket, 1, "conversation.create")[0]
            )["conversation"]["id"]
            run_id = _require_result(
                _rpc_call(
                    websocket,
                    2,
                    "conversation.send",
                    {"conversation_id": conversation_id, "content": "运行"},
                )[0]
            )["run"]["id"]
            message, _ = _rpc_call(websocket, 3, "run.cancel", {"run_id": run_id})
            assert message["error"]["code"] == -32001


def test_run_recover_keeps_old_interrupted(make_app) -> None:
    """recover 语义：旧 Run 保持 INTERRUPTED，新 Run 指向旧 Run。"""

    app, _, _ = make_app()
    with TestClient(app) as client:
        with client.websocket_connect("/rpc") as websocket:
            conversation_id = _require_result(
                _rpc_call(websocket, 1, "conversation.create")[0]
            )["conversation"]["id"]

            # 制造一个 INTERRUPTED Run + 可恢复 Checkpoint。
            application = app.state.application
            old_run_id = client.portal.call(
                lambda: _make_interrupted_run(application, conversation_id)
            )

            message, _ = _rpc_call(
                websocket, 2, "run.recover", {"run_id": old_run_id}
            )
            result = _require_result(message)
            assert result["recovered_from_run_id"] == old_run_id
            new_run_id = result["run"]["id"]
            assert new_run_id != old_run_id
            assert result["run"]["status"] == "completed"

            old = _require_result(
                _rpc_call(websocket, 3, "run.get", {"run_id": old_run_id})[0]
            )["run"]
            assert old["status"] == "interrupted"

            new = _require_result(
                _rpc_call(websocket, 4, "run.get", {"run_id": new_run_id})[0]
            )["run"]
            assert new["recovered_from_run_id"] == old_run_id


async def _make_interrupted_run(
    application: Application,
    conversation_id: str,
) -> str:
    """构造一个带可恢复 Checkpoint 的 INTERRUPTED Run。"""

    run = await application.run_store.create(
        conversation_id=conversation_id,
        user_message="中断任务",
    )
    await application.run_store.mark_started(run.id)
    await application.checkpoint_store.start(
        run.id,
        conversation_id=conversation_id,
        user_message=Message(role=MessageRole.USER, content="中断任务"),
    )
    await application.checkpoint_store.interrupt(run.id, error="simulated stop")
    await application.run_store.mark_interrupted(run.id, error="simulated stop")
    return run.id


def test_trace_get(make_app) -> None:
    app, _, _ = make_app()
    with TestClient(app) as client:
        with client.websocket_connect("/rpc") as websocket:
            conversation_id = _require_result(
                _rpc_call(websocket, 1, "conversation.create")[0]
            )["conversation"]["id"]
            run_id = _require_result(
                _rpc_call(
                    websocket,
                    2,
                    "conversation.send",
                    {"conversation_id": conversation_id, "content": "记录轨迹"},
                )[0]
            )["run"]["id"]

            trace = _require_result(
                _rpc_call(websocket, 3, "trace.get", {"run_id": run_id})[0]
            )
            assert trace["run"]["run_id"] == run_id
            event_types = {event["type"] for event in trace["events"]}
            assert "agent_started" in event_types
            assert "agent_completed" in event_types


# ---------------------------------------------------------------------------
# automation
# ---------------------------------------------------------------------------


def test_automation_crud_and_control(make_app) -> None:
    app, _, _ = make_app()
    with TestClient(app) as client:
        with client.websocket_connect("/rpc") as websocket:
            conversation_id = _require_result(
                _rpc_call(websocket, 1, "conversation.create")[0]
            )["conversation"]["id"]

            created = _require_result(
                _rpc_call(
                    websocket,
                    2,
                    "automation.create",
                    {
                        "title": "每小时检查",
                        "prompt": "检查进度",
                        "kind": "interval",
                        "interval_seconds": 3600,
                        "conversation_id": conversation_id,
                    },
                )[0]
            )
            automation_id = created["automation"]["id"]
            assert created["automation"]["status"] == "active"

            listed = _require_result(_rpc_call(websocket, 3, "automation.list")[0])
            assert any(item["id"] == automation_id for item in listed["automations"])

            detail = _require_result(
                _rpc_call(
                    websocket,
                    4,
                    "automation.get",
                    {"automation_id": automation_id},
                )[0]
            )
            assert detail["automation"]["prompt"] == "检查进度"

            paused = _require_result(
                _rpc_call(
                    websocket,
                    5,
                    "automation.pause",
                    {"automation_id": automation_id},
                )[0]
            )
            assert paused["automation"]["status"] == "paused"
            resumed = _require_result(
                _rpc_call(
                    websocket,
                    6,
                    "automation.resume",
                    {"automation_id": automation_id},
                )[0]
            )
            assert resumed["automation"]["status"] == "active"
            cancelled = _require_result(
                _rpc_call(
                    websocket,
                    7,
                    "automation.cancel",
                    {"automation_id": automation_id},
                )[0]
            )
            assert cancelled["automation"]["status"] == "cancelled"


def test_automation_create_validation(make_app) -> None:
    app, _, _ = make_app()
    with TestClient(app) as client:
        with client.websocket_connect("/rpc") as websocket:
            # once 缺少 run_at → invalid params。
            message, _ = _rpc_call(
                websocket,
                1,
                "automation.create",
                {"title": "t", "prompt": "p", "kind": "once"},
            )
            assert message["error"]["code"] == -32602


def test_automation_run_broadcasts_and_keeps_provenance(make_app) -> None:
    app, _, _ = make_app()
    with TestClient(app) as client:
        with client.websocket_connect("/rpc") as websocket:
            conversation_id = _require_result(
                _rpc_call(websocket, 1, "conversation.create")[0]
            )["conversation"]["id"]
            run_at = (datetime.now(UTC) + timedelta(seconds=1)).isoformat()
            created = _require_result(
                _rpc_call(
                    websocket,
                    2,
                    "automation.create",
                    {
                        "title": "稍后总结",
                        "prompt": "总结项目进度",
                        "kind": "once",
                        "run_at": run_at,
                        "conversation_id": conversation_id,
                    },
                )[0]
            )
            automation_id = created["automation"]["id"]

            # 等 APScheduler 到点自动触发（不手动调用）。
            time.sleep(2.5)

            saw_agent_event = False
            for _ in range(100):
                msg = json.loads(websocket.receive_text())
                if msg.get("method") == "agent.event":
                    saw_agent_event = True
                    if msg["params"].get("type") == "agent_completed":
                        break
            assert saw_agent_event, "automation Run 未广播 agent.event"

            runs = _require_result(
                _rpc_call(
                    websocket,
                    3,
                    "run.list",
                    {"conversation_id": conversation_id},
                )[0]
            )
            automation_runs = [
                run for run in runs["runs"] if run["source"] == "automation"
            ]
            assert automation_runs, "未找到 source=automation 的 Run"
            assert automation_runs[0]["source_id"] == automation_id

            detail = _require_result(
                _rpc_call(
                    websocket,
                    4,
                    "conversation.get",
                    {"conversation_id": conversation_id},
                )[0]
            )
            roles = [msg["role"] for msg in detail["messages"]]
            assert "assistant" in roles


# ---------------------------------------------------------------------------
# Desktop 断开不取消 Run
# ---------------------------------------------------------------------------


def test_disconnect_does_not_cancel_running_run(make_app) -> None:
    app, application, adapter = make_app(blocking=True)
    with TestClient(app) as client:
        with client.websocket_connect("/rpc") as websocket:
            conversation_id = _require_result(
                _rpc_call(websocket, 1, "conversation.create")[0]
            )["conversation"]["id"]
            websocket.send_text(
                _rpc_request(
                    2,
                    "conversation.send",
                    {"conversation_id": conversation_id, "content": "阻塞"},
                )
            )
            for _ in range(200):
                if adapter.started.is_set():
                    break
                time.sleep(0.01)
            assert adapter.started.is_set()
            run_id = None
            for _ in range(50):
                msg = json.loads(websocket.receive_text())
                if (
                    msg.get("method") == "agent.event"
                    and msg["params"].get("type") == "agent_started"
                ):
                    run_id = msg["params"]["run_id"]
                    break
            assert run_id is not None
        # WS 已断开；Run 仍在执行（不因断线被取消）。
        run = client.portal.call(lambda: application.run_manager.get_run(run_id))
        assert run is not None and run.status.value == "running"
        assert adapter.cancelled is False
        # 清理：显式 cancel（不残留后台任务）。
        client.portal.call(lambda: application.run_manager.cancel(run_id))


# ---------------------------------------------------------------------------
# Async Approval V1（approval RPC + 通知 + 不阻塞 WebSocket）
# ---------------------------------------------------------------------------


def test_approval_approve_flow_via_websocket(make_app) -> None:
    """Agent 等待审批时 WebSocket 不阻塞：同一连接可 approval.approve。"""

    app, application, _ = make_app(
        responses=[
            _model_with_tool_call(),
            _model_response(content="审批通过，任务完成"),
        ]
    )
    with TestClient(app) as client:
        with client.websocket_connect("/rpc") as websocket:
            client.portal.call(
                lambda: application.tool_registry.register(ApprovalProbeTool())
            )
            conversation_id = _require_result(
                _rpc_call(websocket, 1, "conversation.create")[0]
            )["conversation"]["id"]

            # conversation.send 会停在审批等待上（不阻塞整个 WebSocket）。
            websocket.send_text(
                _rpc_request(
                    2,
                    "conversation.send",
                    {"conversation_id": conversation_id, "content": "执行审批"},
                )
            )
            required = _drain_until(
                websocket,
                lambda msg: msg.get("method") == "approval.required",
            )
            approval = required["params"]["approval"]
            assert approval["status"] == "pending"
            assert approval["tool_name"] == "approval_probe"
            assert approval["run_id"] is not None
            assert approval["conversation_id"] == conversation_id
            run_id = approval["run_id"]

            # 同一 WebSocket 仍可发送 approval.approve。
            websocket.send_text(
                _rpc_request(
                    3,
                    "approval.approve",
                    {"approval_id": approval["id"]},
                )
            )
            send_response: dict[str, Any] | None = None
            approve_response: dict[str, Any] | None = None
            resolved_seen = False
            while send_response is None or approve_response is None:
                msg = json.loads(websocket.receive_text())
                if msg.get("id") == 2:
                    send_response = msg
                elif msg.get("id") == 3:
                    approve_response = msg
                if msg.get("method") == "approval.resolved":
                    resolved_seen = True
            assert approve_response is not None
            assert approve_response["result"]["approval"]["status"] == "approved"
            assert resolved_seen, "应广播 approval.resolved"

            result = _require_result(send_response)
            assert result["run"]["id"] == run_id
            assert result["run"]["status"] == "completed"
            assert result["result"]["messages"]

            # 审批记录持久化，且 Run 保持 RUNNING→COMPLETED（无新 RunStatus）。
            run = _require_result(
                _rpc_call(websocket, 4, "run.get", {"run_id": run_id})[0]
            )["run"]
            assert run["status"] == "completed"


def test_approval_deny_blocks_tool_execution(make_app) -> None:
    app, application, _ = make_app(
        responses=[
            _model_with_tool_call(),
            _model_response(content="审批被拒绝"),
        ]
    )
    with TestClient(app) as client:
        with client.websocket_connect("/rpc") as websocket:
            probe = ApprovalProbeTool()
            client.portal.call(lambda: application.tool_registry.register(probe))
            conversation_id = _require_result(
                _rpc_call(websocket, 1, "conversation.create")[0]
            )["conversation"]["id"]
            websocket.send_text(
                _rpc_request(
                    2,
                    "conversation.send",
                    {"conversation_id": conversation_id, "content": "执行审批"},
                )
            )
            required = _drain_until(
                websocket,
                lambda msg: msg.get("method") == "approval.required",
            )
            approval_id = required["params"]["approval"]["id"]

            websocket.send_text(
                _rpc_request(
                    3,
                    "approval.deny",
                    {"approval_id": approval_id},
                )
            )
            send_response: dict[str, Any] | None = None
            deny_response: dict[str, Any] | None = None
            while send_response is None or deny_response is None:
                msg = json.loads(websocket.receive_text())
                if msg.get("id") == 2:
                    send_response = msg
                elif msg.get("id") == 3:
                    deny_response = msg
            assert deny_response["result"]["approval"]["status"] == "denied"
            result = _require_result(send_response)
            assert result["run"]["status"] == "completed"
            # 工具未执行（deny → 工具调用失败）。
            assert probe.executions == 0


def test_approval_duplicate_resolve_rejected(make_app) -> None:
    app, application, _ = make_app(
        responses=[
            _model_with_tool_call(),
            _model_response(content="审批通过"),
        ]
    )
    with TestClient(app) as client:
        with client.websocket_connect("/rpc") as websocket:
            client.portal.call(
                lambda: application.tool_registry.register(ApprovalProbeTool())
            )
            conversation_id = _require_result(
                _rpc_call(websocket, 1, "conversation.create")[0]
            )["conversation"]["id"]
            websocket.send_text(
                _rpc_request(
                    2,
                    "conversation.send",
                    {"conversation_id": conversation_id, "content": "执行审批"},
                )
            )
            required = _drain_until(
                websocket,
                lambda msg: msg.get("method") == "approval.required",
            )
            approval_id = required["params"]["approval"]["id"]

            approved, _ = _rpc_call(
                websocket, 3, "approval.approve", {"approval_id": approval_id}
            )
            assert approved["result"]["approval"]["status"] == "approved"

            # 已 resolved 的 approval 再次 approve / deny → INVALID_STATE。
            again, _ = _rpc_call(
                websocket, 4, "approval.approve", {"approval_id": approval_id}
            )
            assert again["error"]["code"] == -32001
            denied, _ = _rpc_call(
                websocket, 5, "approval.deny", {"approval_id": approval_id}
            )
            assert denied["error"]["code"] == -32001


def test_approval_list_and_get(make_app) -> None:
    app, application, _ = make_app(
        responses=[
            _model_with_tool_call(),
            _model_response(content="审批通过"),
        ]
    )
    with TestClient(app) as client:
        with client.websocket_connect("/rpc") as websocket:
            client.portal.call(
                lambda: application.tool_registry.register(ApprovalProbeTool())
            )
            conversation_id = _require_result(
                _rpc_call(websocket, 1, "conversation.create")[0]
            )["conversation"]["id"]
            websocket.send_text(
                _rpc_request(
                    2,
                    "conversation.send",
                    {"conversation_id": conversation_id, "content": "执行审批"},
                )
            )
            required = _drain_until(
                websocket,
                lambda msg: msg.get("method") == "approval.required",
            )
            approval_id = required["params"]["approval"]["id"]

            listed = _require_result(
                _rpc_call(websocket, 3, "approval.list")[0]
            )
            assert any(
                item["id"] == approval_id for item in listed["approvals"]
            )
            pending = _require_result(
                _rpc_call(
                    websocket, 4, "approval.list", {"status": "pending"}
                )[0]
            )
            assert any(item["id"] == approval_id for item in pending["approvals"])

            detail = _require_result(
                _rpc_call(
                    websocket, 5, "approval.get", {"approval_id": approval_id}
                )[0]
            )["approval"]
            assert detail["id"] == approval_id
            assert detail["arguments"]["value"] == 7


def test_approval_not_auto_resolved_on_disconnect(make_app) -> None:
    """Desktop 断线不会自动 approve / deny：审批保持 PENDING，可事后显式决定。"""

    app, application, _ = make_app(
        responses=[
            _model_with_tool_call(),
            _model_response(content="审批通过"),
        ]
    )
    with TestClient(app) as client:
        with client.websocket_connect("/rpc") as websocket:
            client.portal.call(
                lambda: application.tool_registry.register(ApprovalProbeTool())
            )
            conversation_id = _require_result(
                _rpc_call(websocket, 1, "conversation.create")[0]
            )["conversation"]["id"]
            websocket.send_text(
                _rpc_request(
                    2,
                    "conversation.send",
                    {"conversation_id": conversation_id, "content": "执行审批"},
                )
            )
            required = _drain_until(
                websocket,
                lambda msg: msg.get("method") == "approval.required",
            )
            approval_id = required["params"]["approval"]["id"]
            run_id = required["params"]["approval"]["run_id"]
        # WS 已断开：没有自动 approve / deny，记录仍是 PENDING。
        pending = client.portal.call(
            lambda: application.approval_store.get(approval_id)
        )
        assert pending is not None
        assert pending.status.value == "pending"
        # 断线后仍可显式决定（持久化记录可恢复）。
        approved = client.portal.call(
            lambda: application.approval_gate.approve(approval_id)
        )
        assert approved.status.value == "approved"
        # 清理：cancel 阻塞中的 Run。
        client.portal.call(lambda: application.run_manager.cancel(run_id))


def test_automation_run_can_produce_approval(make_app) -> None:
    """Automation 触发的 Run 也走审批链路（approval.required → approve）。"""

    app, application, _ = make_app(
        responses=[
            _model_with_tool_call(),
            _model_response(content="自动化审批完成"),
        ]
    )
    with TestClient(app) as client:
        with client.websocket_connect("/rpc") as websocket:
            client.portal.call(
                lambda: application.tool_registry.register(ApprovalProbeTool())
            )
            conversation_id = _require_result(
                _rpc_call(websocket, 1, "conversation.create")[0]
            )["conversation"]["id"]
            run_at = (datetime.now(UTC) + timedelta(seconds=1)).isoformat()
            created = _require_result(
                _rpc_call(
                    websocket,
                    2,
                    "automation.create",
                    {
                        "title": "自动审批任务",
                        "prompt": "执行审批",
                        "kind": "once",
                        "run_at": run_at,
                        "conversation_id": conversation_id,
                    },
                )[0]
            )
            automation_id = created["automation"]["id"]

            # 等 APScheduler 触发 → Run 停在审批 → 广播 approval.required。
            required = _drain_until(
                websocket,
                lambda msg: msg.get("method") == "approval.required",
            )
            approval = required["params"]["approval"]
            assert approval["status"] == "pending"
            assert approval["tool_name"] == "approval_probe"

            # 批准 → 自动化 Run 继续并完成。
            websocket.send_text(
                _rpc_request(
                    3,
                    "approval.approve",
                    {"approval_id": approval["id"]},
                )
            )
            completed = _drain_until(
                websocket,
                lambda msg: (
                    msg.get("method") == "agent.event"
                    and msg["params"].get("type") == "agent_completed"
                ),
            )
            run_id = completed["params"]["run_id"]

            # agent_completed 事件先于 Run DB 终态写入，轮询到 completed。
            detail: dict[str, Any] = {}
            for _ in range(200):
                detail = _require_result(
                    _rpc_call(websocket, 4, "run.get", {"run_id": run_id})[0]
                )["run"]
                if detail["status"] == "completed":
                    break
                time.sleep(0.02)
            assert detail["status"] == "completed"
            assert detail["source"] == "automation"
            assert detail["source_id"] == automation_id


# ---------------------------------------------------------------------------
# Plan Mode V1（conversation.send mode + task RPC）
# ---------------------------------------------------------------------------


def test_plan_mode_send_creates_pending_task_and_accept(make_app) -> None:
    app, _, _ = make_app(
        responses=[
            _model_response(
                tool_calls=(
                    ToolCall(
                        id="plan-1",
                        name="task_create",
                        arguments={
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
            _model_response(content="计划已形成"),
        ]
    )
    with TestClient(app) as client:
        with client.websocket_connect("/rpc") as websocket:
            conversation_id = _require_result(
                _rpc_call(websocket, 1, "conversation.create")[0]
            )["conversation"]["id"]
            message, _ = _rpc_call(
                websocket,
                2,
                "conversation.send",
                {
                    "conversation_id": conversation_id,
                    "content": "帮我实现 Computer Runtime",
                    "mode": "plan",
                },
            )
            result = _require_result(message)
            assert result["run"]["mode"] == "plan"
            assert result["plan_task_id"]
            plan_task_id = result["plan_task_id"]

            # Plan Mode 生成的 Task 为 PENDING。
            detail = _require_result(
                _rpc_call(websocket, 3, "task.get", {"task_id": plan_task_id})[0]
            )["task"]
            assert detail["status"] == "pending"
            assert detail["goal"] == "实现 Computer Runtime V1"
            assert len(detail["steps"]) == 3

            # accept: PENDING → ACTIVE。
            accepted = _require_result(
                _rpc_call(
                    websocket, 4, "task.plan_accept", {"task_id": plan_task_id}
                )[0]
            )["task"]
            assert accepted["status"] == "active"

            # 已 ACTIVE 不能再次 accept / reject。
            again = _rpc_call(
                websocket, 5, "task.plan_accept", {"task_id": plan_task_id}
            )[0]
            assert again["error"]["code"] == -32001
            reject = _rpc_call(
                websocket, 6, "task.plan_reject", {"task_id": plan_task_id}
            )[0]
            assert reject["error"]["code"] == -32001


def test_plan_mode_send_reject_task(make_app) -> None:
    app, _, _ = make_app(
        responses=[
            _model_response(
                tool_calls=(
                    ToolCall(
                        id="plan-1",
                        name="task_create",
                        arguments={
                            "title": "另一个计划",
                            "goal": "目标",
                            "steps": [{"title": "第一步"}],
                        },
                    ),
                )
            ),
            _model_response(content="计划已形成"),
        ]
    )
    with TestClient(app) as client:
        with client.websocket_connect("/rpc") as websocket:
            conversation_id = _require_result(
                _rpc_call(websocket, 1, "conversation.create")[0]
            )["conversation"]["id"]
            result = _require_result(
                _rpc_call(
                    websocket,
                    2,
                    "conversation.send",
                    {
                        "conversation_id": conversation_id,
                        "content": "规划",
                        "mode": "plan",
                    },
                )[0]
            )
            plan_task_id = result["plan_task_id"]
            rejected = _require_result(
                _rpc_call(
                    websocket, 3, "task.plan_reject", {"task_id": plan_task_id}
                )[0]
            )["task"]
            assert rejected["status"] == "cancelled"


def test_plan_mode_send_blocks_side_effect_tool(make_app) -> None:
    app, _, _ = make_app(
        responses=[
            _model_response(
                tool_calls=(
                    ToolCall(
                        id="bad-1",
                        name="write_file",
                        arguments={"path": "evil.txt", "content": "x"},
                    ),
                )
            ),
            _model_response(content="不应能写文件"),
        ]
    )
    with TestClient(app) as client:
        with client.websocket_connect("/rpc") as websocket:
            conversation_id = _require_result(
                _rpc_call(websocket, 1, "conversation.create")[0]
            )["conversation"]["id"]
            result = _require_result(
                _rpc_call(
                    websocket,
                    2,
                    "conversation.send",
                    {
                        "conversation_id": conversation_id,
                        "content": "写个文件",
                        "mode": "plan",
                    },
                )[0]
            )
            assert result["run"]["mode"] == "plan"
            # 副作用工具被阻断：tool 结果失败。
            tool_result = result["result"]["tool_calls"][0]["result"]
            assert tool_result["success"] is False
            assert "not allowed in plan mode" in (tool_result["error"] or "")
            # 未创建 Task → 明确提示。
            assert result["plan_task_id"] is None


def test_conversation_send_default_mode_is_normal(make_app) -> None:
    app, _, _ = make_app()
    with TestClient(app) as client:
        with client.websocket_connect("/rpc") as websocket:
            conversation_id = _require_result(
                _rpc_call(websocket, 1, "conversation.create")[0]
            )["conversation"]["id"]
            result = _require_result(
                _rpc_call(
                    websocket,
                    2,
                    "conversation.send",
                    {"conversation_id": conversation_id, "content": "你好"},
                )[0]
            )
            assert result["run"]["mode"] == "normal"


def test_conversation_send_invalid_mode(make_app) -> None:
    app, _, _ = make_app()
    with TestClient(app) as client:
        with client.websocket_connect("/rpc") as websocket:
            conversation_id = _require_result(
                _rpc_call(websocket, 1, "conversation.create")[0]
            )["conversation"]["id"]
            message, _ = _rpc_call(
                websocket,
                2,
                "conversation.send",
                {
                    "conversation_id": conversation_id,
                    "content": "hi",
                    "mode": "bogus",
                },
            )
            assert message["error"]["code"] == -32602


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------


def test_server_shutdown_closes_resources(make_app) -> None:
    app, application, _ = make_app()
    with TestClient(app) as client:
        with client.websocket_connect("/rpc") as websocket:
            _require_result(_rpc_call(websocket, 1, "system.info")[0])
        assert application._started is True
    # lifespan finally 已执行 application.close()。
    assert application._started is False
    assert application.automation_scheduler._running == set()
    assert application.automation_scheduler._job_ids == {}


async def test_application_start_close_idempotent(tmp_path: Path) -> None:
    config = ProviderConfig(
        provider="fake",
        model="fake-model",
        api_key=SecretStr("offline-test-key"),
        api_style=ApiStyle.CHAT_COMPLETIONS,
    )
    adapter = RepeatingFakeAdapter(config, _model_response("ok"))
    registry = ModelAdapterRegistry(ModelSettings(_env_file=None))
    registry.register("fake", lambda _: adapter, config=config)

    application = Application(
        provider="fake",
        model="fake-model",
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
    await application.start()
    await application.start()  # 幂等
    assert application._started is True
    await application.close()
    await application.close()  # 幂等
    assert application._started is False
