"""MacOSComputerRuntime（V1 open_app + V2/V3 observe + V4 click）测试。

使用 stub HelperClient，不启动真实 GUI App、不调用 NSWorkspace /
AXUIElement / AXPress、不需要 Accessibility / Screen Recording 权限。

覆盖：
V1 open_app：
1. open_app 确实发送 method=open_app / params={app}
2. helper result → ActionResult（success / action / metadata）
3. helper error 正确向上传播
4. 空 app 在 runtime 层被拒绝

V2/V3 observe：
5. observe 生成 observation_id 并传给 helper，且回填 Observation.id
6. active_app / active_window / bounds 正确转换
7. helper elements 正确转成 Element（role/title/value/enabled/focused/bounds/actions）
8. windows 包含 active_window、空 elements、screenshot_ref=None
9. helper permission error 正确向上传播
10. open_app 现有实现不受影响

V4 click：
11. ElementTarget → click_element（observation_id / element_ref 正确）
12. helper success → ActionResult（action==CLICK、observation_id 保留、metadata）
13. stale_observation / element_not_found error 正确向上传播
14. CoordinateTarget 明确拒绝

其它 type/key/scroll/focus_window 仍然 NotImplementedError
"""

from __future__ import annotations

import pytest

from app.computer import (
    ActionName,
    ActionResult,
    ComputerHelperError,
    CoordinateTarget,
    ElementTarget,
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
# V2/V3 observe
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


def _observe_result_with_elements() -> dict:
    result = _observe_result()
    result["elements"] = [
        {
            "ref": "e1",
            "role": "text_area",
            "value": "hello world",
            "enabled": True,
            "focused": True,
            "bounds": {"x": 10, "y": 20, "width": 300, "height": 200},
            "actions": [],
        },
        {
            "ref": "e2",
            "role": "button",
            "title": "Save",
            "enabled": True,
            "focused": False,
            "bounds": {"x": 100, "y": 100, "width": 80, "height": 30},
            "actions": ["press"],
        },
    ]
    return result


async def test_observe_generates_and_passes_observation_id() -> None:
    stub = StubHelperClient(result=_observe_result())
    runtime = _runtime(stub)

    obs = await runtime.observe()

    assert len(stub.calls) == 1
    method, params = stub.calls[0]
    assert method == "observe"
    observation_id = params["observation_id"]
    assert isinstance(observation_id, str) and len(observation_id) == 32
    # observation id 由 Python 生成并回填 Observation.id。
    assert obs.id == observation_id


async def test_observe_converts_elements() -> None:
    runtime = _runtime(
        StubHelperClient(result=_observe_result_with_elements())
    )

    obs = await runtime.observe()

    assert len(obs.elements) == 2
    e1, e2 = obs.elements
    assert e1.ref == "e1"
    assert e1.role == "text_area"
    assert e1.value == "hello world"
    assert e1.title is None
    assert e1.enabled is True
    assert e1.focused is True
    assert e1.actions == ()
    assert e1.bounds is not None
    assert (
        e1.bounds.x, e1.bounds.y, e1.bounds.width, e1.bounds.height
    ) == (10, 20, 300, 200)

    assert e2.ref == "e2"
    assert e2.role == "button"
    assert e2.title == "Save"
    assert e2.value is None
    assert e2.focused is False
    assert e2.actions == ("press",)
    assert e2.bounds is not None
    assert e2.bounds.x == 100 and e2.bounds.y == 100
    assert e2.bounds.width == 80 and e2.bounds.height == 30


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
            "observe": _observe_result(),
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
    assert [m for m, _ in stub.calls] == ["observe", "open_app"]


# ---------------------------------------------------------------------------
# V4 click（ElementTarget → AXPress）
# ---------------------------------------------------------------------------


def _click_result() -> dict:
    return {"observation_id": "obs-1", "element_ref": "e1", "action": "press"}


async def test_click_element_target_sends_request() -> None:
    stub = StubHelperClient(result=_click_result())
    runtime = _runtime(stub)

    result = await runtime.click(
        ElementTarget(observation_id="obs-1", element_ref="e1")
    )

    assert stub.calls == [
        ("click_element", {"observation_id": "obs-1", "element_ref": "e1"})
    ]
    assert result.success is True


async def test_click_converts_to_action_result() -> None:
    stub = StubHelperClient(result=_click_result())
    runtime = _runtime(stub)

    result = await runtime.click(
        ElementTarget(observation_id="obs-1", element_ref="e1")
    )

    assert isinstance(result, ActionResult)
    assert result.success is True
    assert result.action is ActionName.CLICK
    assert result.observation_id == "obs-1"
    assert result.metadata["element_ref"] == "e1"
    assert result.metadata["method"] == "ax_press"
    assert result.metadata["action"] == "press"


async def test_click_stale_observation_error_propagates() -> None:
    stub = StubHelperClient(
        error=ComputerHelperError("stale_observation: stale_observation")
    )
    runtime = _runtime(stub)

    with pytest.raises(ComputerHelperError, match="stale_observation"):
        await runtime.click(
            ElementTarget(observation_id="obs-old", element_ref="e1")
        )


async def test_click_element_not_found_error_propagates() -> None:
    stub = StubHelperClient(
        error=ComputerHelperError("element_not_found: element_not_found")
    )
    runtime = _runtime(stub)

    with pytest.raises(ComputerHelperError, match="element_not_found"):
        await runtime.click(
            ElementTarget(observation_id="obs-1", element_ref="e99")
        )


async def test_click_coordinate_target_rejected() -> None:
    stub = StubHelperClient(result={})
    runtime = _runtime(stub)

    with pytest.raises(NotImplementedError, match="coordinate click"):
        await runtime.click(
            CoordinateTarget(observation_id="obs-1", x=10, y=20)
        )
    # CoordinateTarget 不应发送任何请求。
    assert stub.calls == []


async def test_click_does_not_break_observe_and_open_app() -> None:
    stub = StubHelperClient(
        per_method={
            "observe": _observe_result(),
            "click_element": _click_result(),
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

    click_result = await runtime.click(
        ElementTarget(observation_id="obs-1", element_ref="e1")
    )
    assert click_result.action is ActionName.CLICK

    open_result = await runtime.open_app("TextEdit")
    assert open_result.success is True

    assert [m for m, _ in stub.calls] == [
        "observe",
        "click_element",
        "open_app",
    ]


# ---------------------------------------------------------------------------
# 其它方法仍然 NotImplementedError
# ---------------------------------------------------------------------------


async def test_other_runtime_methods_still_not_implemented() -> None:
    runtime = _runtime(StubHelperClient(result={}))

    for coro in (
        runtime.type("hi"),
        runtime.key("enter"),
        runtime.scroll(),
        runtime.focus_window("w1"),
    ):
        with pytest.raises(NotImplementedError, match="open_app"):
            await coro
