"""MacOSComputerRuntime（V1 open_app + V2 observe）测试。

使用 stub HelperClient，不启动真实 GUI App、不调用 NSWorkspace /
AXUIElement、不需要 Accessibility / Screen Recording 权限。

覆盖：
V1 open_app：
1. open_app 确实发送 method=open_app / params={app}
2. helper result → ActionResult（success / action / metadata）
3. helper error 正确向上传播
4. 空 app 在 runtime 层被拒绝

V2 observe：
5. observe 调用 basic_observe 且正确转换 active_app / active_window / bounds
6. windows 包含 active_window、elements 为空、screenshot_ref=None
7. helper permission error 正确向上传播
8. open_app 现有实现不受影响

其它 click/type/key/scroll/focus_window 仍然 NotImplementedError
"""

from __future__ import annotations

import pytest

from app.computer import (
    ActionName,
    ActionResult,
    ComputerHelperError,
    MacOSComputerRuntime,
)


class StubHelperClient:
    """记录 call 并返回预设结果 / 抛预设异常的 HelperClient stub。

    - ``result``：所有 method 的默认返回；
    - ``per_method``：按 method 覆盖返回值（优先级最高）；
    - ``error``：所有 method 都抛该异常。
    """

    def __init__(
        self,
        *,
        result: dict | None = None,
        error: Exception | None = None,
        per_method: dict[str, dict] | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.per_method = per_method or {}
        self.calls: list[tuple[str, dict]] = []

    async def call(self, method: str, params: dict | None = None, **kwargs):
        self.calls.append((method, params or {}))
        if self.error is not None:
            raise self.error
        if method in self.per_method:
            return self.per_method[method]
        return self.result if self.result is not None else {}


def _runtime(stub: StubHelperClient) -> MacOSComputerRuntime:
    return MacOSComputerRuntime(stub)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 1. open_app 调 helper
# ---------------------------------------------------------------------------


async def test_open_app_sends_request_to_helper() -> None:
    stub = StubHelperClient(
        result={
            "app": "TextEdit",
            "bundle_id": "com.apple.TextEdit",
            "process_id": 4242,
        }
    )
    runtime = _runtime(stub)

    result = await runtime.open_app("TextEdit")

    assert stub.calls == [("open_app", {"app": "TextEdit"})]
    assert result.success is True


# ---------------------------------------------------------------------------
# 2. helper result → ActionResult
# ---------------------------------------------------------------------------


async def test_open_app_converts_to_action_result() -> None:
    stub = StubHelperClient(
        result={
            "app": "TextEdit",
            "bundle_id": "com.apple.TextEdit",
            "process_id": 4242,
        }
    )
    runtime = _runtime(stub)

    result = await runtime.open_app("TextEdit")

    assert isinstance(result, ActionResult)
    assert result.success is True
    assert result.action is ActionName.OPEN_APP
    assert result.metadata["app"] == "TextEdit"
    assert result.metadata["bundle_id"] == "com.apple.TextEdit"
    assert result.metadata["process_id"] == 4242
    assert result.error is None


async def test_open_app_bundle_id_hint() -> None:
    # 以 bundle id 形式传入也要原样转发给 helper。
    stub = StubHelperClient(
        result={
            "app": "com.apple.TextEdit",
            "bundle_id": "com.apple.TextEdit",
            "process_id": 7,
        }
    )
    runtime = _runtime(stub)

    result = await runtime.open_app("com.apple.TextEdit")

    assert stub.calls == [("open_app", {"app": "com.apple.TextEdit"})]
    assert result.metadata["bundle_id"] == "com.apple.TextEdit"


# ---------------------------------------------------------------------------
# 3. helper error 正确向上传播
# ---------------------------------------------------------------------------


async def test_open_app_helper_error_propagates() -> None:
    stub = StubHelperClient(
        error=ComputerHelperError("app_not_found: TextEdit")
    )
    runtime = _runtime(stub)

    with pytest.raises(ComputerHelperError, match="app_not_found"):
        await runtime.open_app("TextEdit")


async def test_open_app_launch_failed_propagates() -> None:
    stub = StubHelperClient(
        error=ComputerHelperError("app_launch_failed: TextEdit")
    )
    runtime = _runtime(stub)

    with pytest.raises(ComputerHelperError, match="app_launch_failed"):
        await runtime.open_app("TextEdit")


# ---------------------------------------------------------------------------
# 4. 空 app 拒绝
# ---------------------------------------------------------------------------


async def test_open_app_rejects_empty_app() -> None:
    stub = StubHelperClient(result={})
    runtime = _runtime(stub)

    with pytest.raises(ValueError, match="non-empty"):
        await runtime.open_app("")
    with pytest.raises(ValueError, match="non-empty"):
        await runtime.open_app("   ")
    with pytest.raises(ValueError, match="non-empty"):
        await runtime.open_app(None)  # type: ignore[arg-type]
    # 校验失败时不应发送任何请求。
    assert stub.calls == []


# ---------------------------------------------------------------------------
# V2 observe
# ---------------------------------------------------------------------------


def _observe_result() -> dict:
    return {
        "active_app": {
            "name": "TextEdit",
            "bundle_id": "com.apple.TextEdit",
            "process_id": 1234,
        },
        "active_window": {
            "title": "Untitled",
            "bounds": {"x": 100, "y": 80, "width": 900, "height": 700},
        },
    }


async def test_observe_calls_basic_observe() -> None:
    stub = StubHelperClient(result=_observe_result())
    runtime = _runtime(stub)

    obs = await runtime.observe()

    assert stub.calls == [("basic_observe", {})]
    assert obs.id  # observation id 由 Python model 生成，不由 helper 提供


async def test_observe_converts_active_app() -> None:
    runtime = _runtime(StubHelperClient(result=_observe_result()))

    obs = await runtime.observe()

    assert obs.active_app is not None
    assert obs.active_app.name == "TextEdit"
    assert obs.active_app.bundle_id == "com.apple.TextEdit"
    assert obs.active_app.pid == 1234


async def test_observe_converts_active_window() -> None:
    runtime = _runtime(StubHelperClient(result=_observe_result()))

    obs = await runtime.observe()

    assert obs.active_window is not None
    assert obs.active_window.ref == "w1"
    assert obs.active_window.title == "Untitled"


async def test_observe_converts_bounds() -> None:
    runtime = _runtime(StubHelperClient(result=_observe_result()))

    obs = await runtime.observe()
    assert obs.active_window is not None
    bounds = obs.active_window.bounds
    assert bounds.x == 100
    assert bounds.y == 80
    assert bounds.width == 900
    assert bounds.height == 700


async def test_observe_windows_contains_active_window() -> None:
    runtime = _runtime(StubHelperClient(result=_observe_result()))

    obs = await runtime.observe()

    assert obs.active_window is not None
    assert obs.windows == (obs.active_window,)
    assert len(obs.windows) == 1


async def test_observe_elements_empty_and_no_screenshot() -> None:
    runtime = _runtime(StubHelperClient(result=_observe_result()))

    obs = await runtime.observe(include_screenshot=True)

    assert obs.elements == ()
    assert obs.screenshot_ref is None


async def test_observe_permission_error_propagates() -> None:
    stub = StubHelperClient(
        error=ComputerHelperError(
            "accessibility_permission_required: "
            "macOS Accessibility permission is required"
        )
    )
    runtime = _runtime(stub)

    with pytest.raises(
        ComputerHelperError, match="accessibility_permission_required"
    ):
        await runtime.observe()


async def test_observe_no_active_window() -> None:
    runtime = _runtime(
        StubHelperClient(
            result={
                "active_app": {
                    "name": "Finder",
                    "bundle_id": "com.apple.finder",
                    "process_id": 1,
                },
                "active_window": None,
            }
        )
    )

    obs = await runtime.observe()

    assert obs.active_app is not None
    assert obs.active_app.name == "Finder"
    assert obs.active_window is None
    assert obs.windows == ()


async def test_open_app_still_works_after_observe() -> None:
    stub = StubHelperClient(
        per_method={
            "basic_observe": _observe_result(),
            "open_app": {
                "app": "TextEdit",
                "bundle_id": "com.apple.TextEdit",
                "process_id": 5,
            },
        }
    )
    runtime = _runtime(stub)

    obs = await runtime.observe()
    assert obs.active_app is not None and obs.active_app.name == "TextEdit"

    result = await runtime.open_app("TextEdit")
    assert result.success is True
    assert result.metadata["process_id"] == 5
    assert [m for m, _ in stub.calls] == ["basic_observe", "open_app"]


# ---------------------------------------------------------------------------
# 其它方法仍然 NotImplementedError
# ---------------------------------------------------------------------------


async def test_other_runtime_methods_still_not_implemented() -> None:
    runtime = _runtime(StubHelperClient(result={}))

    for coro in (
        runtime.click(None),  # type: ignore[arg-type]
        runtime.type("hi"),
        runtime.key("enter"),
        runtime.scroll(),
        runtime.focus_window("w1"),
    ):
        with pytest.raises(NotImplementedError, match="open_app"):
            await coro
