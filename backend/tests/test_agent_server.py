"""Agent Server 测试（全部用离线 fake model，不调用真实模型 API）。

覆盖：health / conversation CRUD / send message 走 ConversationService /
写回 / run list·detail·cancel / trace API / automation CRUD·control /
WebSocket 收到 AgentEvent / automation Run 也能广播 / shutdown 正确关闭资源。
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
    """阻塞在模型请求上，直到被取消（用于 cancel 测试）。"""

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


def _create_conversation(client: TestClient) -> dict:
    response = client.post("/api/conversations", json={})
    assert response.status_code == 200
    return response.json()["conversation"]


# ---------------------------------------------------------------------------
# 1. health
# ---------------------------------------------------------------------------


def test_health(make_app) -> None:
    app, application, _ = make_app()
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["provider"] == "fake"
        assert body["model"] == "fake-model"


# ---------------------------------------------------------------------------
# 2. conversation create / list / get
# ---------------------------------------------------------------------------


def test_conversation_create_list_get(make_app) -> None:
    app, _, _ = make_app()
    with TestClient(app) as client:
        conversation = _create_conversation(client)
        assert conversation["title"] == "新会话"
        conversation_id = conversation["id"]

        listed = client.get("/api/conversations").json()
        assert any(item["id"] == conversation_id for item in listed["conversations"])

        detail = client.get(f"/api/conversations/{conversation_id}").json()
        assert detail["conversation"]["id"] == conversation_id
        assert detail["messages"] == []


# ---------------------------------------------------------------------------
# 3. send message 走 ConversationService + 4. Conversation 写回
# ---------------------------------------------------------------------------


def test_send_message_goes_through_conversation_service_and_writes_back(
    make_app,
) -> None:
    app, application, _ = make_app()
    with TestClient(app) as client:
        conversation = _create_conversation(client)
        conversation_id = conversation["id"]

        calls: list[dict] = []
        original_dispatch = application.conversation_service.dispatch

        async def spy_dispatch(**kwargs: object):
            calls.append(kwargs)
            return await original_dispatch(**kwargs)

        application.conversation_service.dispatch = spy_dispatch  # type: ignore[method-assign]

        response = client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": "帮我总结进度"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["conversation_id"] == conversation_id
        assert body["run"]["conversation_id"] == conversation_id
        assert body["content"] == "已完成"

        # 确实经 ConversationService.dispatch。
        assert calls, "dispatch 未被调用"
        assert calls[0]["conversation_id"] == conversation_id
        assert calls[0]["content"] == "帮我总结进度"

        # 新会话第一次发送 → 标题由首条消息生成。
        renamed = client.get(f"/api/conversations/{conversation_id}").json()
        assert renamed["conversation"]["title"] == "帮我总结进度"

        # Conversation 已写回 user + assistant。
        roles = [message["role"] for message in renamed["messages"]]
        assert "user" in roles
        assert "assistant" in roles


def test_send_message_to_missing_conversation_404(make_app) -> None:
    app, _, _ = make_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/conversations/nope/messages",
            json={"content": "hi"},
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# 5. run list / detail
# ---------------------------------------------------------------------------


def test_run_list_and_detail(make_app) -> None:
    app, _, _ = make_app()
    with TestClient(app) as client:
        conversation = _create_conversation(client)
        conversation_id = conversation["id"]
        response = client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": "运行一次"},
        )
        run_id = response.json()["run"]["id"]

        listed = client.get("/api/runs").json()
        assert any(item["id"] == run_id for item in listed["runs"])

        filtered = client.get(f"/api/runs?conversation_id={conversation_id}").json()
        assert any(item["id"] == run_id for item in filtered["runs"])

        detail = client.get(f"/api/runs/{run_id}").json()["run"]
        assert detail["id"] == run_id
        assert detail["status"] == "completed"
        assert detail["source"] == "manual"
        assert detail["conversation_id"] == conversation_id


# ---------------------------------------------------------------------------
# 6. cancel
# ---------------------------------------------------------------------------


def test_cancel_terminal_run_conflict(make_app) -> None:
    app, _, _ = make_app()
    with TestClient(app) as client:
        conversation = _create_conversation(client)
        response = client.post(
            f"/api/conversations/{conversation['id']}/messages",
            json={"content": "运行"},
        )
        run_id = response.json()["run"]["id"]
        # 已完成的 Run 不能被取消 → 409。
        cancel = client.post(f"/api/runs/{run_id}/cancel")
        assert cancel.status_code == 409


def test_cancel_running_run(make_app) -> None:
    app, application, adapter = make_app(blocking=True)
    with TestClient(app) as client:
        conversation = _create_conversation(client)
        # 在应用事件循环里启动一个阻塞 Run（同步测试无法通过 POST 制造 running）。
        run_id, _task = client.portal.call(
            lambda: application.run_manager.start(
                "阻塞任务",
                conversation_id=conversation["id"],
            )
        )
        # 等待模型请求真正开始，确保 Run 处于 RUNNING。
        for _ in range(100):
            if adapter.started.is_set():
                break
            time.sleep(0.01)
        assert adapter.started.is_set()

        response = client.post(f"/api/runs/{run_id}/cancel")
        assert response.status_code == 200
        assert response.json()["run"]["status"] == "cancelled"
        assert adapter.cancelled is True


# ---------------------------------------------------------------------------
# 7. trace API
# ---------------------------------------------------------------------------


def test_trace_api(make_app) -> None:
    app, _, _ = make_app()
    with TestClient(app) as client:
        conversation = _create_conversation(client)
        response = client.post(
            f"/api/conversations/{conversation['id']}/messages",
            json={"content": "执行并记录轨迹"},
        )
        run_id = response.json()["run"]["id"]

        trace = client.get(f"/api/runs/{run_id}/trace")
        assert trace.status_code == 200
        body = trace.json()
        assert body["run"]["run_id"] == run_id
        event_types = {event["type"] for event in body["events"]}
        assert "agent_started" in event_types
        assert "model_started" in event_types
        assert "agent_completed" in event_types

        missing = client.get("/api/runs/nope/trace")
        assert missing.status_code == 404


# ---------------------------------------------------------------------------
# 8. automation CRUD / control
# ---------------------------------------------------------------------------


def test_automation_crud_and_control(make_app) -> None:
    app, _, _ = make_app()
    with TestClient(app) as client:
        conversation = _create_conversation(client)
        created = client.post(
            "/api/automations",
            json={
                "title": "每小时检查",
                "prompt": "检查进度",
                "kind": "interval",
                "interval_seconds": 3600,
                "conversation_id": conversation["id"],
            },
        )
        assert created.status_code == 200
        automation = created.json()["automation"]
        automation_id = automation["id"]
        assert automation["status"] == "active"
        assert automation["conversation_id"] == conversation["id"]

        listed = client.get("/api/automations").json()
        assert any(item["id"] == automation_id for item in listed["automations"])

        detail = client.get(f"/api/automations/{automation_id}").json()["automation"]
        assert detail["prompt"] == "检查进度"

        paused = client.post(f"/api/automations/{automation_id}/pause").json()
        assert paused["automation"]["status"] == "paused"

        resumed = client.post(f"/api/automations/{automation_id}/resume").json()
        assert resumed["automation"]["status"] == "active"

        cancelled = client.post(f"/api/automations/{automation_id}/cancel").json()
        assert cancelled["automation"]["status"] == "cancelled"


def test_automation_create_validation(make_app) -> None:
    app, _, _ = make_app()
    with TestClient(app) as client:
        # 缺少 run_at 的 once → 422。
        response = client.post(
            "/api/automations",
            json={"title": "t", "prompt": "p", "kind": "once"},
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# 9. WebSocket 收到 AgentEvent
# ---------------------------------------------------------------------------


def test_websocket_receives_agent_events(make_app) -> None:
    app, _, _ = make_app()
    with TestClient(app) as client:
        conversation = _create_conversation(client)
        with client.websocket_connect("/api/events") as websocket:
            response = client.post(
                f"/api/conversations/{conversation['id']}/messages",
                json={"content": "实时看一下"},
            )
            assert response.status_code == 200

            seen: set[str] = set()
            run_statuses: list[dict] = []
            for _ in range(100):
                message = websocket.receive_json()
                if message["type"] == "agent_event":
                    seen.add(message["data"]["type"])
                elif message["type"] == "run_status":
                    run_statuses.append(message["data"])
                if (
                    "agent_completed" in seen
                    and any(item["status"] == "completed" for item in run_statuses)
                ):
                    break
            assert "agent_started" in seen
            assert "model_started" in seen
            assert "model_completed" in seen
            assert "agent_completed" in seen
            assert any(item["status"] == "running" for item in run_statuses)
            assert any(item["status"] == "completed" for item in run_statuses)


# ---------------------------------------------------------------------------
# 10. automation Run 也能广播 event + 产生 source=automation 的 Run
# ---------------------------------------------------------------------------


def test_automation_run_broadcasts_and_creates_source_automation_run(make_app) -> None:
    app, application, _ = make_app()
    with TestClient(app) as client:
        conversation = _create_conversation(client)
        with client.websocket_connect("/api/events") as websocket:
            run_at = (datetime.now(UTC) + timedelta(seconds=1)).isoformat()
            created = client.post(
                "/api/automations",
                json={
                    "title": "稍后总结",
                    "prompt": "总结项目进度",
                    "kind": "once",
                    "run_at": run_at,
                    "conversation_id": conversation["id"],
                },
            )
            assert created.status_code == 200
            automation_id = created.json()["automation"]["id"]

            # 等 APScheduler 到点自动触发（不手动调用 _trigger）。
            time.sleep(2.5)

            seen_agent_events = False
            for _ in range(100):
                message = websocket.receive_json()
                if message["type"] == "agent_event":
                    seen_agent_events = True
                if message["type"] == "agent_event" and message["data"]["type"] == (
                    "agent_completed"
                ):
                    break
            assert seen_agent_events, "automation Run 未广播 AgentEvent"

        # Runs 页面能看到 source=automation 的 Run。
        runs = client.get(f"/api/runs?conversation_id={conversation['id']}").json()
        automation_runs = [
            run for run in runs["runs"] if run["source"] == "automation"
        ]
        assert automation_runs, "未找到 source=automation 的 Run"
        assert automation_runs[0]["source_id"] == automation_id

        # Conversation 中出现 Automation 执行结果。
        detail = client.get(f"/api/conversations/{conversation['id']}").json()
        roles = [message["role"] for message in detail["messages"]]
        assert "assistant" in roles


# ---------------------------------------------------------------------------
# 11. shutdown 正确关闭资源
# ---------------------------------------------------------------------------


def test_server_shutdown_closes_resources(make_app) -> None:
    app, application, _ = make_app()
    with TestClient(app) as client:
        _create_conversation(client)
        client.get("/health")
        assert application._started is True
    # lifespan finally 已执行 application.close()。
    assert application._started is False
    # Scheduler 已 shutdown：不再残留 job / running 集合。
    assert application.automation_scheduler._running == set()
    assert application.automation_scheduler._job_ids == {}


# ---------------------------------------------------------------------------
# 12. application bootstrap 幂等：start/close 可安全重复
# ---------------------------------------------------------------------------


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
