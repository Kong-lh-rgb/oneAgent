"""computer RPC 测试：状态 / 权限请求 / latest observation / screenshot 边界。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from app.agent.events import AgentEvent, AgentEventType
from app.application import Application
from app.computer import ComputerHostStatus, MacOSComputerRuntime
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
    ToolResult,
)
from app.server.app import computer_screenshot
from app.server.rpc.dispatcher import RpcContext
from app.server.rpc.methods import computer as computer_rpc
from app.server.rpc.protocol import (
    INVALID_STATE,
    JsonRpcError,
    RpcErrorCode,
)


class StubHelperClient:
    """记录 call 并返回预设结果的 HelperClient stub。"""

    def __init__(self, per_method: dict | None = None) -> None:
        self.per_method = per_method or {}
        self.calls: list[tuple[str, dict]] = []
        self.started = False

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.started = False

    async def ensure_started(self) -> None:
        if not self.started:
            await self.start()

    async def call(self, method: str, params: dict | None = None, **kwargs):
        self.calls.append((method, params or {}))
        if method in self.per_method:
            result = self.per_method[method]
            if isinstance(result, Exception):
                raise result
            return result
        return {}


class _OfflineAdapter(ModelAdapter):
    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)

    async def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            id="fake-response",
            provider="fake",
            model="fake-model",
            message=Message(role=MessageRole.ASSISTANT, content="ok"),
            usage=ModelUsage(),
        )

    async def close(self) -> None:
        pass


async def _build_application(
    tmp_path,
    *,
    computer_runtime=None,
    computer_host_status: ComputerHostStatus | None = None,
) -> Application:
    from app.memory import MemoryMaintenanceConfig, MemoryReflectionConfig
    from app.skill_learning import SkillLearningSettings

    config = ProviderConfig(
        provider="fake",
        model="fake-model",
        api_key=SecretStr("offline-test-key"),
        api_style=ApiStyle.CHAT_COMPLETIONS,
    )
    adapter = _OfflineAdapter(config)
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
        computer_host_status=computer_host_status,
    )
    await application.start()
    return application


def _ctx(app: Application) -> RpcContext:
    return RpcContext(application=app, connection=None)  # type: ignore[arg-type]


def _available_status() -> ComputerHostStatus:
    return ComputerHostStatus(
        enabled=True,
        available=True,
        platform="macos",
        reason=None,
        helper_path="/tmp/helper",
        runtime="macos",
    )


def _observation_payload(obs_id: str = "obs-1") -> dict:
    return {
        "id": obs_id,
        "created_at": "2026-08-20T00:00:00+00:00",
        "active_app": {
            "name": "TextEdit",
            "bundle_id": "com.apple.TextEdit",
            "pid": 100,
        },
        "active_window": {
            "ref": "w1",
            "title": "Untitled",
            "bounds": {"x": 0, "y": 0, "width": 800, "height": 600},
        },
        "windows": [],
        "elements": [
            {
                "ref": "e1",
                "role": "text_area",
                "title": None,
                "value": "hi",
                "enabled": True,
                "focused": True,
                "bounds": None,
                "actions": [],
            }
        ],
        "screenshot_ref": f"{obs_id}.png",
    }


async def _seed_observe(
    app: Application,
    run_id: str,
    *,
    seq: int,
    success: bool = True,
    output: str | None = None,
) -> None:
    event = AgentEvent(
        run_id=run_id,
        conversation_id="conv-1",
        sequence=seq,
        type=AgentEventType.TOOL_COMPLETED,
        tool_call=ToolCall(
            id=f"t{seq}", name="computer_observe", arguments={}
        ),
        tool_result=ToolResult(
            tool_call_id=f"t{seq}",
            tool_name="computer_observe",
            success=success,
            output=output,
            duration_ms=1.0,
        ),
    )
    await app.trace_store.record_event(event)


# ---------------------------------------------------------------------------
# computer.status
# ---------------------------------------------------------------------------


async def test_status_runtime_absent(tmp_path) -> None:
    app = await _build_application(tmp_path, computer_runtime=None)
    try:
        result = await computer_rpc.computer_status({}, _ctx(app))
        assert result["enabled"] is False
        assert result["available"] is False
        assert result["reason"] == "not_configured"
        assert result["runtime"] is None
        assert result["permissions"] == {
            "accessibility": "unknown",
            "screen_recording": "unknown",
        }
        assert result["lease"] is None
    finally:
        await app.close()


async def test_status_runtime_available(tmp_path) -> None:
    stub = StubHelperClient(
        per_method={
            "accessibility_status": {"trusted": True},
            "screen_capture_status": {"granted": True},
        }
    )
    runtime = MacOSComputerRuntime(stub)  # type: ignore[arg-type]
    app = await _build_application(
        tmp_path, computer_runtime=runtime, computer_host_status=_available_status()
    )
    try:
        result = await computer_rpc.computer_status({}, _ctx(app))
        assert result["available"] is True
        assert result["runtime"] == "macos"
        assert result["permissions"] == {
            "accessibility": "granted",
            "screen_recording": "granted",
        }
        # status 不应触发 prompt。
        accessibility_calls = [
            params for m, params in stub.calls if m == "accessibility_status"
        ]
        assert accessibility_calls == [{}]
    finally:
        await app.close()


async def test_status_reports_missing_permissions(tmp_path) -> None:
    stub = StubHelperClient(
        per_method={
            "accessibility_status": {"trusted": True},
            "screen_capture_status": {"granted": False},
        }
    )
    runtime = MacOSComputerRuntime(stub)  # type: ignore[arg-type]
    app = await _build_application(
        tmp_path, computer_runtime=runtime, computer_host_status=_available_status()
    )
    try:
        result = await computer_rpc.computer_status({}, _ctx(app))
        assert result["permissions"]["accessibility"] == "granted"
        assert result["permissions"]["screen_recording"] == "required"
    finally:
        await app.close()


async def test_status_unknown_when_helper_errors(tmp_path) -> None:
    stub = StubHelperClient(
        per_method={
            "accessibility_status": RuntimeError("boom"),
            "screen_capture_status": RuntimeError("boom"),
        }
    )
    runtime = MacOSComputerRuntime(stub)  # type: ignore[arg-type]
    app = await _build_application(
        tmp_path, computer_runtime=runtime, computer_host_status=_available_status()
    )
    try:
        result = await computer_rpc.computer_status({}, _ctx(app))
        assert result["permissions"] == {
            "accessibility": "unknown",
            "screen_recording": "unknown",
        }
    finally:
        await app.close()


# ---------------------------------------------------------------------------
# computer.request_permission
# ---------------------------------------------------------------------------


async def test_request_permission_accessibility(tmp_path) -> None:
    stub = StubHelperClient(
        per_method={
            "accessibility_status": {"trusted": False},
            "screen_capture_status": {"granted": True},
        }
    )
    runtime = MacOSComputerRuntime(stub)  # type: ignore[arg-type]
    app = await _build_application(
        tmp_path, computer_runtime=runtime, computer_host_status=_available_status()
    )
    try:
        result = await computer_rpc.computer_request_permission(
            {"permission": "accessibility"}, _ctx(app)
        )
        assert result["permissions"]["accessibility"] == "required"
        assert ("accessibility_status", {"prompt": True}) in stub.calls
    finally:
        await app.close()


async def test_request_permission_screen_recording(tmp_path) -> None:
    stub = StubHelperClient(
        per_method={
            "accessibility_status": {"trusted": True},
            "screen_capture_status": {"granted": True},
        }
    )
    runtime = MacOSComputerRuntime(stub)  # type: ignore[arg-type]
    app = await _build_application(
        tmp_path, computer_runtime=runtime, computer_host_status=_available_status()
    )
    try:
        await computer_rpc.computer_request_permission(
            {"permission": "screen_recording"}, _ctx(app)
        )
        assert ("screen_capture_status", {"prompt": True}) in stub.calls
    finally:
        await app.close()


async def test_request_permission_invalid_rejected(tmp_path) -> None:
    app = await _build_application(tmp_path, computer_runtime=None)
    try:
        with pytest.raises(JsonRpcError) as exc:
            await computer_rpc.computer_request_permission(
                {"permission": "camera"}, _ctx(app)
            )
        assert exc.value.code == RpcErrorCode.INVALID_PARAMS
    finally:
        await app.close()


async def test_request_permission_unavailable_rejected(tmp_path) -> None:
    app = await _build_application(tmp_path, computer_runtime=None)
    try:
        with pytest.raises(JsonRpcError) as exc:
            await computer_rpc.computer_request_permission(
                {"permission": "accessibility"}, _ctx(app)
            )
        assert exc.value.code == INVALID_STATE
    finally:
        await app.close()


# ---------------------------------------------------------------------------
# computer.latest_observation
# ---------------------------------------------------------------------------


async def test_latest_observation_finds_computer_observe(tmp_path) -> None:
    app = await _build_application(tmp_path, computer_runtime=None)
    try:
        await _seed_observe(
            app,
            "run-1",
            seq=1,
            output=json.dumps(_observation_payload("obs-1")),
        )
        result = await computer_rpc.computer_latest_observation(
            {"run_id": "run-1"}, _ctx(app)
        )
        assert result["run_id"] == "run-1"
        assert result["observation"]["id"] == "obs-1"
        assert result["event_time"] is not None
    finally:
        await app.close()


async def test_latest_observation_takes_most_recent(tmp_path) -> None:
    app = await _build_application(tmp_path, computer_runtime=None)
    try:
        await _seed_observe(
            app, "run-1", seq=1, output=json.dumps(_observation_payload("obs-1"))
        )
        await _seed_observe(
            app, "run-1", seq=2, output=json.dumps(_observation_payload("obs-2"))
        )
        result = await computer_rpc.computer_latest_observation(
            {"run_id": "run-1"}, _ctx(app)
        )
        assert result["observation"]["id"] == "obs-2"
    finally:
        await app.close()


async def test_latest_observation_skips_failed(tmp_path) -> None:
    app = await _build_application(tmp_path, computer_runtime=None)
    try:
        await _seed_observe(
            app, "run-1", seq=1, output=json.dumps(_observation_payload("obs-1"))
        )
        await _seed_observe(app, "run-1", seq=2, success=False, output="{}")
        result = await computer_rpc.computer_latest_observation(
            {"run_id": "run-1"}, _ctx(app)
        )
        # 最新的 failed 被跳过，回退到之前的成功 observe。
        assert result["observation"]["id"] == "obs-1"
    finally:
        await app.close()


async def test_latest_observation_skips_malformed_output(tmp_path) -> None:
    app = await _build_application(tmp_path, computer_runtime=None)
    try:
        await _seed_observe(app, "run-1", seq=1, output="not json")
        result = await computer_rpc.computer_latest_observation(
            {"run_id": "run-1"}, _ctx(app)
        )
        assert result["observation"] is None
    finally:
        await app.close()


async def test_latest_observation_malformed_then_earlier_valid(tmp_path) -> None:
    app = await _build_application(tmp_path, computer_runtime=None)
    try:
        await _seed_observe(
            app, "run-1", seq=1, output=json.dumps(_observation_payload("obs-1"))
        )
        await _seed_observe(app, "run-1", seq=2, output="broken")
        result = await computer_rpc.computer_latest_observation(
            {"run_id": "run-1"}, _ctx(app)
        )
        assert result["observation"]["id"] == "obs-1"
    finally:
        await app.close()


async def test_latest_observation_none_when_no_observation(tmp_path) -> None:
    app = await _build_application(tmp_path, computer_runtime=None)
    try:
        await _seed_observe(
            app, "run-1", seq=1, output=json.dumps({"id": "obs-1"})
        )
        result = await computer_rpc.computer_latest_observation(
            {"run_id": "other-run"}, _ctx(app)
        )
        assert result["observation"] is None
    finally:
        await app.close()


async def test_latest_observation_uses_lease_owner_when_no_run_id(tmp_path) -> None:
    stub = StubHelperClient()
    runtime = MacOSComputerRuntime(stub)  # type: ignore[arg-type]
    app = await _build_application(
        tmp_path, computer_runtime=runtime, computer_host_status=_available_status()
    )
    try:
        await _seed_observe(
            app,
            "run-lease",
            seq=1,
            output=json.dumps(_observation_payload("obs-lease")),
        )
        assert app.computer_lease is not None
        app.computer_lease.acquire("run-lease")
        result = await computer_rpc.computer_latest_observation({}, _ctx(app))
        assert result["run_id"] == "run-lease"
        assert result["observation"]["id"] == "obs-lease"
    finally:
        await app.close()


async def test_latest_observation_invalid_run_id(tmp_path) -> None:
    app = await _build_application(tmp_path, computer_runtime=None)
    try:
        with pytest.raises(JsonRpcError) as exc:
            await computer_rpc.computer_latest_observation(
                {"run_id": 123}, _ctx(app)
            )
        assert exc.value.code == RpcErrorCode.INVALID_PARAMS
    finally:
        await app.close()


# ---------------------------------------------------------------------------
# screenshot endpoint 边界
# ---------------------------------------------------------------------------


def _fake_request(fake_app, host: str = "127.0.0.1"):
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(application=fake_app)),
        client=SimpleNamespace(host=host),
    )


def _fake_app_with_runtime(screenshot_dir: Path, runtime=None):
    if runtime is None:
        runtime = SimpleNamespace(screenshot_dir=str(screenshot_dir))
    return SimpleNamespace(computer_runtime=runtime)


async def test_screenshot_valid_returns_png(tmp_path) -> None:
    screenshot_dir = tmp_path / "screenshots"
    screenshot_dir.mkdir(parents=True)
    obs_id = "a" * 32
    (screenshot_dir / f"{obs_id}.png").write_bytes(b"PNGDATA")

    fake_app = _fake_app_with_runtime(screenshot_dir)
    response = computer_screenshot(obs_id, _fake_request(fake_app))

    assert response.status_code == 200
    assert response.media_type == "image/png"
    assert response.headers["cache-control"] == "no-store"
    assert response.body == b"PNGDATA"


async def test_screenshot_missing_returns_404(tmp_path) -> None:
    screenshot_dir = tmp_path / "screenshots"
    screenshot_dir.mkdir(parents=True)
    fake_app = _fake_app_with_runtime(screenshot_dir)

    with pytest.raises(HTTPException) as exc:
        computer_screenshot("b" * 32, _fake_request(fake_app))
    assert exc.value.status_code == 404


async def test_screenshot_path_traversal_impossible(tmp_path) -> None:
    screenshot_dir = tmp_path / "screenshots"
    screenshot_dir.mkdir(parents=True)
    (screenshot_dir / "evil.png").write_bytes(b"X")
    fake_app = _fake_app_with_runtime(screenshot_dir)

    for bad in ("..", "../evil", "..%2Fevil", "evil", "a" * 31, "a" * 33):
        with pytest.raises(HTTPException) as exc:
            computer_screenshot(bad, _fake_request(fake_app))
        assert exc.value.status_code in (403, 404)


async def test_screenshot_non_loopback_rejected(tmp_path) -> None:
    screenshot_dir = tmp_path / "screenshots"
    screenshot_dir.mkdir(parents=True)
    obs_id = "c" * 32
    (screenshot_dir / f"{obs_id}.png").write_bytes(b"PNGDATA")
    fake_app = _fake_app_with_runtime(screenshot_dir)

    with pytest.raises(HTTPException) as exc:
        computer_screenshot(
            obs_id, _fake_request(fake_app, host="10.0.0.5")
        )
    assert exc.value.status_code == 403


async def test_screenshot_unavailable_returns_404(tmp_path) -> None:
    fake_app = SimpleNamespace(computer_runtime=None)
    with pytest.raises(HTTPException) as exc:
        computer_screenshot("d" * 32, _fake_request(fake_app))
    assert exc.value.status_code == 404
