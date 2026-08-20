"""Agent Server 测试（全部用离线 fake model，不调用真实模型 API）。

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
)
from app.server.app import create_app
from app.skill_learning import SkillLearningSettings


def _model_response(content: str = "已完成") -> ModelResponse:
    return ModelResponse(
        id="fake-response",
        provider="fake",
        model="fake-model",
        message=Message(
            role=MessageRole.ASSISTANT,
            content=content,
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
    """构造一个可注入 fake registry 的 Application + FastAPI app。"""

    def build(*, blocking: bool = False, response_text: str = "已完成"):
        config = ProviderConfig(
            provider="fake",
            model="fake-model",
            api_key=SecretStr("offline-test-key"),
            api_style=ApiStyle.CHAT_COMPLETIONS,
        )
        if blocking:
            adapter: ModelAdapter = BlockingFakeAdapter(config)
        else:
            adapter = RepeatingFakeAdapter(config, _model_response(response_text))
        registry = ModelAdapterRegistry(ModelSettings(_env_file=None))
        registry.register("fake", lambda _: adapter, config=config)

        application = Application(
            provider="fake",
            model="fake-model",
            database=tmp_path / "oneagent.db",
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
        database=tmp_path / "oneagent.db",
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
