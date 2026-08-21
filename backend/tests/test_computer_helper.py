"""MacOSHelperClient / MacOSComputerRuntime V0 测试。

使用测试用假 helper（tests/fixtures/fake_computer_helper.py）实现同样的
JSON Lines 协议，不需要 Swift 构建、不需要任何 macOS 权限。

覆盖（对应需求 11）：
1-3. start / ping / system_info
4. request id correlation（__echo_id 回显收到的 id）
5. 并发 call 正确返回且 id 各自对应
6. unknown method 返回明确错误
7. close 后进程退出
8. helper 意外退出时 pending Future 被 reject
9. malformed response / 非 JSON / 未知 id 被正确处理
10. Application close 会关闭已启动的 helper
+ MacOSComputerRuntime 生命周期由 Application 正确启动和关闭。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.application import Application
from app.computer import (
    ComputerHelperError,
    ComputerHelperProcessError,
    ComputerHelperProtocolError,
    ComputerLeaseManager,
    MacOSComputerRuntime,
    MacOSHelperClient,
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


def _helper_command() -> tuple[str, tuple[str, ...]]:
    script = Path(__file__).resolve().parent / "fixtures" / "fake_computer_helper.py"
    return sys.executable, (str(script),)


def _make_client() -> MacOSHelperClient:
    exe, args = _helper_command()
    return MacOSHelperClient(exe, helper_args=args)


# ---------------------------------------------------------------------------
# 1-3. start / ping / system_info
# ---------------------------------------------------------------------------


async def test_client_can_start_and_ping() -> None:
    client = _make_client()
    await client.start()
    try:
        assert client._process is not None
        assert client._process.returncode is None  # 进程存活
        result = await client.call("ping", {})
        assert result == {"ok": True}
    finally:
        await client.close()


async def test_system_info() -> None:
    client = _make_client()
    await client.start()
    try:
        result = await client.call("system_info", {})
        assert result["platform"] == "macos"
        assert result["helper_version"] == "0.0.1-test"
        assert result["process_id"] > 0
    finally:
        await client.close()


async def test_call_before_start_raises() -> None:
    client = _make_client()
    with pytest.raises(ComputerHelperProcessError, match="not started"):
        await client.call("ping", {})


# ---------------------------------------------------------------------------
# 4. request id correlation
# ---------------------------------------------------------------------------


async def test_request_id_correlation() -> None:
    client = _make_client()
    await client.start()
    try:
        # __echo_id 把收到的请求 id 回显到 result。
        first = await client.call("__echo_id", {})
        second = await client.call("__echo_id", {})
        third = await client.call("ping", {})
        assert first["received_id"] == 1
        assert second["received_id"] == 2
        assert third == {"ok": True}
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# 5. 并发 call
# ---------------------------------------------------------------------------


async def test_concurrent_calls_correlate_correctly() -> None:
    client = _make_client()
    await client.start()
    try:
        results = await asyncio.gather(
            client.call("ping", {}),
            client.call("__echo_id", {}),
            client.call("system_info", {}),
            client.call("__echo_id", {}),
        )
        assert results[0] == {"ok": True}
        assert results[1]["received_id"] == 2
        assert results[2]["platform"] == "macos"
        assert results[2]["process_id"] > 0
        assert results[3]["received_id"] == 4
    finally:
        await client.close()


async def test_concurrent_unknown_method_and_ping() -> None:
    client = _make_client()
    await client.start()
    try:
        ping, error_result = await asyncio.gather(
            client.call("ping", {}),
            client.call("__bogus", {}),
            return_exceptions=True,
        )
        assert ping == {"ok": True}
        assert isinstance(error_result, ComputerHelperError)
        assert "unknown_method" in str(error_result)
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# 6. unknown method
# ---------------------------------------------------------------------------


async def test_unknown_method_returns_clear_error() -> None:
    client = _make_client()
    await client.start()
    try:
        with pytest.raises(ComputerHelperError, match="unknown_method"):
            await client.call("__no_such_method", {})
        # helper 未崩溃，继续可用。
        assert await client.call("ping", {}) == {"ok": True}
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# 7. close 后进程退出
# ---------------------------------------------------------------------------


async def test_close_stops_process() -> None:
    client = _make_client()
    await client.start()
    process = client._process
    assert process is not None
    await client.close()
    assert process.returncode == 0  # stdin EOF → 正常退出
    assert client._process is None
    # close 幂等。
    await client.close()


# ---------------------------------------------------------------------------
# 8. helper 意外退出时 pending Future 被 reject
# ---------------------------------------------------------------------------


async def test_helper_crash_rejects_pending() -> None:
    client = _make_client()
    await client.start()
    try:
        with pytest.raises(ComputerHelperProcessError, match="exited"):
            await client.call("__crash", {})
        # 进程已退出：后续调用给出明确错误。
        with pytest.raises(ComputerHelperProcessError, match="not started"):
            await client.call("ping", {})
        await client.restart()
        assert await client.call("ping", {}) == {"ok": True}
    finally:
        await client.close()


async def test_observe_recovers_dead_helper_as_a_new_safe_request(tmp_path) -> None:
    client = _make_client()
    runtime = MacOSComputerRuntime(client, screenshot_dir=tmp_path)
    runtime.begin_session("test-run")
    await runtime.start()
    try:
        with pytest.raises(ComputerHelperProcessError):
            await client.call("__crash", {})
        observation = await runtime.observe(include_screenshot=False)
        assert observation.active_app is not None
        assert observation.active_app.name == "FakeApp"
    finally:
        await runtime.close()


# ---------------------------------------------------------------------------
# 9. malformed / 非 JSON / 未知 id 响应
# ---------------------------------------------------------------------------


async def test_malformed_response_rejected() -> None:
    client = _make_client()
    await client.start()
    try:
        # __malformed：有 id 但既无 result 也无 error。
        with pytest.raises(ComputerHelperProtocolError, match="malformed"):
            await client.call("__malformed", {})
        # helper 存活，继续可用。
        assert await client.call("ping", {}) == {"ok": True}
    finally:
        await client.close()


async def test_bad_json_line_breaks_protocol_until_restart() -> None:
    client = _make_client()
    await client.start()
    try:
        # __bad_json：先写一行非 JSON，再正常回 error。
        with pytest.raises(ComputerHelperProtocolError, match="malformed JSON"):
            await client.call("__bad_json", {})
        await client.restart()
        assert await client.call("ping", {}) == {"ok": True}
    finally:
        await client.close()


async def test_unknown_id_breaks_protocol_until_restart() -> None:
    client = _make_client()
    await client.start()
    try:
        # __unknown_id：先发一条无主响应（id 不对应任何 pending），再回 error。
        with pytest.raises(ComputerHelperProtocolError, match="unknown id"):
            await client.call("__unknown_id", {})
        await client.restart()
        assert await client.call("ping", {}) == {"ok": True}
    finally:
        await client.close()


@pytest.mark.parametrize("method", ["__invalid_id", "__non_object"])
async def test_invalid_response_shape_breaks_protocol(method: str) -> None:
    client = _make_client()
    await client.start()
    try:
        with pytest.raises(ComputerHelperProtocolError):
            await client.call(method, {})
        await client.restart()
        assert await client.call("ping", {}) == {"ok": True}
    finally:
        await client.close()


async def test_timeout_late_response_is_ignored() -> None:
    client = _make_client()
    await client.start()
    try:
        with pytest.raises(ComputerHelperError, match="timed out"):
            await client.call("__delay", {"seconds": 0.05}, timeout=0.005)
        # helper 先发退休 id 的迟到响应，再处理 ping；连接仍健康。
        assert await client.call("ping", {}, timeout=1) == {"ok": True}
        assert client._pending == {}
    finally:
        await client.close()


async def test_cancelled_call_does_not_leak_pending() -> None:
    client = _make_client()
    await client.start()
    try:
        task = asyncio.create_task(client.call("__delay", {"seconds": 0.05}))
        await asyncio.sleep(0.005)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert client._pending == {}
        assert await client.call("ping", {}, timeout=1) == {"ok": True}
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# open_app 传输层（使用 fake helper，不启动真实 App）
# ---------------------------------------------------------------------------


async def test_open_app_transport_returns_result() -> None:
    client = _make_client()
    await client.start()
    try:
        result = await client.call("open_app", {"app": "TextEdit"})
        assert result["app"] == "TextEdit"
        assert result["bundle_id"] == "com.example.TextEdit"
        assert result["process_id"] == 4242
    finally:
        await client.close()


async def test_open_app_missing_app_invalid_params() -> None:
    client = _make_client()
    await client.start()
    try:
        with pytest.raises(ComputerHelperError, match="invalid_params"):
            await client.call("open_app", {})
        with pytest.raises(ComputerHelperError, match="invalid_params"):
            await client.call("open_app", {"app": "   "})
    finally:
        await client.close()


async def test_open_app_concurrent_with_ping() -> None:
    client = _make_client()
    await client.start()
    try:
        open_result, ping_result = await asyncio.gather(
            client.call("open_app", {"app": "Notes"}),
            client.call("ping", {}),
        )
        assert open_result["app"] == "Notes"
        assert ping_result == {"ok": True}
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# MacOSComputerRuntime 骨架
# ---------------------------------------------------------------------------


async def test_macos_runtime_lifecycle_start_close() -> None:
    client = _make_client()
    runtime = MacOSComputerRuntime(client)
    assert runtime.helper_client is client

    await runtime.start()
    process = client._process
    assert process is not None and process.returncode is None
    await runtime.close()
    assert process.returncode == 0


# ---------------------------------------------------------------------------
# 10. Application 生命周期
# ---------------------------------------------------------------------------


class _OfflineAdapter(ModelAdapter):
    """离线模型适配器（Application 启动用，不真正调用模型）。"""

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


async def _build_application(tmp_path, *, computer_runtime):
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
        memory_reflection_config=MemoryReflectionConfig(_env_file=None, enabled=False),
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


async def test_application_start_starts_macos_runtime(tmp_path) -> None:
    client = _make_client()
    runtime = MacOSComputerRuntime(client)
    app = await _build_application(tmp_path, computer_runtime=runtime)
    try:
        assert app.computer_runtime is runtime
        process = client._process
        assert process is not None and process.returncode is None
    finally:
        await app.close()


async def test_application_close_closes_helper(tmp_path) -> None:
    client = _make_client()
    runtime = MacOSComputerRuntime(client)
    app = await _build_application(tmp_path, computer_runtime=runtime)
    process = client._process
    assert process is not None and process.returncode is None
    await app.close()
    assert process.returncode == 0  # helper 已随 Application.close 关闭
    assert client._process is None


async def test_application_close_releases_machine_lease(tmp_path) -> None:
    client = _make_client()
    app = await _build_application(
        tmp_path, computer_runtime=MacOSComputerRuntime(client)
    )
    assert app.computer_lease is not None
    lock_path = app.computer_lease.lock_path
    app.computer_lease.acquire("run-a")
    await app.close()

    next_host = ComputerLeaseManager(lock_path)
    try:
        assert next_host.acquire("run-b").owner_run_id == "run-b"
    finally:
        next_host.close()
