"""MacOSComputerRuntime 原生能力的离线 Stub 测试。

使用 stub HelperClient，不启动真实 GUI App、不调用 NSWorkspace /
AXUIElement / AXPress / CGEvent、不需要 Accessibility / Screen Recording
权限。

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

V5 type：
15. type("hello") 调用 type_text，params.text 正确
16. helper result → ActionResult（action==TYPE、metadata 只有 characters）
17. 空字符串 / 非字符串行为正确
18. permission error 正确向上传播
19. open_app / observe / click 不受影响

V6 key：
20. key / modifiers 正确传给 key_press
21. helper result 正确转成 ActionResult.KEY
22. helper 错误向上传播，非法 Python 参数在本地拒绝

V7 scroll / focus_window / screenshot / coordinate click：
23. 多窗口与 active_window_ref 转换
24. 截图路径与截图失败降级
25. scroll / focus / coordinate 请求和 ActionResult
26. Python 最新 Observation 生命周期
"""

from __future__ import annotations

import pytest

from app.computer import (
    ActionName,
    ActionResult,
    ComputerHelperError,
    ComputerHelperProcessError,
    ComputerHelperProtocolError,
    CoordinateTarget,
    Element,
    ElementTarget,
    MacOSComputerRuntime,
    Observation,
    VerificationStatus,
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
        self.session_ids: list[str | None] = []

    async def ensure_started(self) -> None:
        return None

    async def call(self, method: str, params: dict | None = None, **kwargs):
        params = params or {}
        # V2：session_id 单独记录，不影响既有断言；新协议由专门用例验证。
        self.session_ids.append(params.pop("session_id", None))
        self.calls.append((method, params))
        if self.error is not None:
            raise self.error
        if method in self.per_method:
            return self.per_method[method]
        return self.result if self.result is not None else {}


def _runtime(stub: StubHelperClient) -> MacOSComputerRuntime:
    runtime = MacOSComputerRuntime(stub)  # type: ignore[arg-type]
    # V2：所有请求都需要 Run-scoped active session；测试用固定 run 建立。
    runtime.begin_session("test-run")
    # Snapshot 单一真源在 ComputerSession.current_snapshot。
    _attach(runtime, Observation(
        id="obs-current",
        elements=(
            Element(ref="e1", role="text_area", focused=True, editable=True),
            Element(ref="e2", role="text_field", editable=True),
        ),
    ))
    return runtime


def _snapshot(runtime: MacOSComputerRuntime) -> Observation | None:
    session = runtime._session_manager.get_active()
    return session.current_snapshot if session else None


def _attach(runtime: MacOSComputerRuntime, observation: Observation) -> None:
    runtime._session_manager.require_active().attach_snapshot(observation)


async def test_begin_session_rpc_requires_explicit_native_acceptance() -> None:
    stub = StubHelperClient(per_method={"begin_session": {"accepted": True}})
    runtime = MacOSComputerRuntime(stub)  # type: ignore[arg-type]

    session = await runtime.begin_session_rpc("run-a")
    repeated = await runtime.begin_session_rpc("run-a")

    assert runtime._session_manager.get("run-a") is session
    assert repeated is session
    assert stub.calls == [("begin_session", {}), ("begin_session", {})]
    assert stub.session_ids == [session.session_id, session.session_id]


@pytest.mark.parametrize("result", [{}, {"accepted": False}])
async def test_begin_session_rpc_fails_closed_without_native_acceptance(
    result: dict,
) -> None:
    runtime = MacOSComputerRuntime(  # type: ignore[arg-type]
        StubHelperClient(per_method={"begin_session": result})
    )

    with pytest.raises(ComputerHelperProtocolError):
        await runtime.begin_session_rpc("run-a")

    assert runtime._session_manager.get_active() is None


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
    assert result.metadata["frontmost_verified"] is False
    assert result.error is None


async def test_open_app_preserves_frontmost_verification() -> None:
    runtime = _runtime(
        StubHelperClient(
            result={
                "app": "Notes",
                "bundle_id": "com.apple.Notes",
                "process_id": 4242,
                "frontmost_verified": True,
            }
        )
    )

    result = await runtime.open_app("Notes")

    assert result.metadata["frontmost_verified"] is True


async def test_open_app_launch_success_does_not_require_frontmost() -> None:
    runtime = _runtime(
        StubHelperClient(
            result={
                "app": "Notes",
                "bundle_id": "com.apple.Notes",
                "process_id": 4242,
                "launch_status": "running",
                "activation_status": "not_frontmost",
                "frontmost_verified": False,
            }
        )
    )

    result = await runtime.open_app("Notes")

    assert result.success is True
    assert result.metadata["launch_status"] == "running"
    assert result.metadata["activation_status"] == "not_frontmost"


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
    stub = StubHelperClient(error=ComputerHelperError("app_not_found: TextEdit"))
    runtime = _runtime(stub)

    with pytest.raises(ComputerHelperError, match="app_not_found"):
        await runtime.open_app("TextEdit")


async def test_open_app_launch_failed_propagates() -> None:
    stub = StubHelperClient(error=ComputerHelperError("app_launch_failed: TextEdit"))
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
            "editable": True,
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
    runtime = _runtime(StubHelperClient(result=_observe_result_with_elements()))

    obs = await runtime.observe()

    assert len(obs.elements) == 2
    e1, e2 = obs.elements
    assert e1.ref == "e1"
    assert e1.role == "text_area"
    assert e1.value == "hello world"
    assert e1.title is None
    assert e1.enabled is True
    assert e1.focused is True
    assert e1.editable is True
    assert e1.actions == ()
    assert e1.bounds is not None
    assert (e1.bounds.x, e1.bounds.y, e1.bounds.width, e1.bounds.height) == (
        10,
        20,
        300,
        200,
    )

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


async def test_observe_converts_stable_target_and_element_stats() -> None:
    raw = _observe_result_with_elements()
    raw.update(
        {
            "target": {
                "name": "Notes",
                "bundle_id": "com.apple.Notes",
                "process_id": 9876,
            },
            "target_is_frontmost": False,
            "truncated": True,
            "element_stats": {
                "observed": 1200,
                "returned": 300,
                "editable_count": 2,
                "actionable_count": 8,
                "repetitive_elements_dropped": 820,
            },
        }
    )

    obs = await _runtime(StubHelperClient(result=raw)).observe()

    assert obs.target is not None
    assert obs.target.name == "Notes"
    assert obs.target.pid == 9876
    assert obs.target_is_frontmost is False
    assert obs.truncated is True
    assert obs.element_stats.observed == 1200
    assert obs.element_stats.returned == 300
    assert obs.element_stats.editable_count == 2
    assert obs.element_stats.actionable_count == 8
    assert obs.element_stats.repetitive_elements_dropped == 820


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

    with pytest.raises(ComputerHelperError, match="accessibility_permission_required"):
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
    assert result.method == "ax_press"
    assert result.metadata["element_ref"] == "e1"
    assert result.metadata["action"] == "press"


async def test_click_stale_observation_error_propagates() -> None:
    stub = StubHelperClient(
        error=ComputerHelperError("stale_observation: stale_observation")
    )
    runtime = _runtime(stub)

    with pytest.raises(ComputerHelperError, match="stale_observation"):
        await runtime.click(ElementTarget(observation_id="obs-old", element_ref="e1"))


async def test_click_element_not_found_error_propagates() -> None:
    stub = StubHelperClient(
        error=ComputerHelperError("element_not_found: element_not_found")
    )
    runtime = _runtime(stub)

    with pytest.raises(ComputerHelperError, match="element_not_found"):
        await runtime.click(ElementTarget(observation_id="obs-1", element_ref="e99"))


async def test_click_coordinate_target_calls_helper() -> None:
    stub = StubHelperClient(result={"x": 10, "y": 20})
    runtime = _runtime(stub)

    result = await runtime.click(CoordinateTarget(observation_id="obs-1", x=10, y=20))
    assert stub.calls == [
        ("click_coordinate", {"observation_id": "obs-1", "x": 10, "y": 20})
    ]
    assert result.method == "coordinate"
    assert result.metadata == {"x": 10, "y": 20}


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
# V5 type（CGEvent Unicode 文本输入）
# ---------------------------------------------------------------------------


def _type_result(characters: int = 14) -> dict:
    return {
        "characters": characters,
        "delivery_status": "delivered",
        "verification_status": "verified",
    }


async def test_type_calls_type_text() -> None:
    stub = StubHelperClient(result=_type_result())
    runtime = _runtime(stub)

    result = await runtime.type("Hello Vesta")

    assert stub.calls == [
        (
            "type_text",
            {
                "text": "Hello Vesta",
                "expected_observation_id": "obs-current",
                "element_ref": "e1",
            },
        )
    ]
    assert result.success is True


async def test_type_converts_to_action_result() -> None:
    stub = StubHelperClient(result=_type_result(14))
    runtime = _runtime(stub)

    result = await runtime.type("Hello Vesta")

    assert isinstance(result, ActionResult)
    assert result.action is ActionName.TYPE
    assert result.metadata == {
        "characters": 14,
        "element_ref": "e1",
        "evidence": {},
    }
    # 不保存完整 text（避免复制长/敏感内容）。
    assert "text" not in result.metadata


async def test_type_empty_string() -> None:
    stub = StubHelperClient(result=_type_result(0))
    runtime = _runtime(stub)

    result = await runtime.type("")

    assert stub.calls == [
        (
            "type_text",
            {
                "text": "",
                "expected_observation_id": "obs-current",
                "element_ref": "e1",
            },
        )
    ]
    assert result.metadata["characters"] == 0


async def test_type_non_string_rejected() -> None:
    stub = StubHelperClient(result=_type_result())
    runtime = _runtime(stub)

    with pytest.raises(ValueError, match="must be a string"):
        await runtime.type(123)  # type: ignore[arg-type]
    # 校验失败不应发送请求。
    assert stub.calls == []


async def test_type_with_element_ref() -> None:
    stub = StubHelperClient(result=_type_result())
    runtime = _runtime(stub)

    result = await runtime.type("Hello", element_ref="e2")

    assert stub.calls == [
        (
            "type_text",
            {
                "text": "Hello",
                "expected_observation_id": "obs-current",
                "element_ref": "e2",
            },
        )
    ]
    assert result.success is True


async def test_type_element_ref_validation() -> None:
    stub = StubHelperClient(result=_type_result())
    runtime = _runtime(stub)

    with pytest.raises(ValueError, match="element_ref"):
        await runtime.type("hi", element_ref="")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="element_ref"):
        await runtime.type("hi", element_ref="  ")
    assert stub.calls == []


async def test_type_rejects_non_editable_or_unknown_target() -> None:
    runtime = _runtime(StubHelperClient(result=_type_result()))
    _attach(runtime, Observation(
        id="obs-current",
        elements=(Element(ref="button", role="button"),),
    ))

    with pytest.raises(ValueError, match="editable_target_required"):
        await runtime.type("hi")
    with pytest.raises(ValueError, match="element_not_editable"):
        await runtime.type("hi", element_ref="button")
    with pytest.raises(ValueError, match="latest observation"):
        await runtime.type("hi", element_ref="missing")


async def test_type_rejects_non_text_value_settable_control() -> None:
    runtime = _runtime(StubHelperClient(result=_type_result()))
    _attach(runtime, Observation(
        id="obs-current",
        elements=(
            Element(ref="split", role="splitter", editable=True),
            Element(ref="editor", role="text_area", editable=True),
        ),
    ))

    with pytest.raises(ValueError, match="text-entry"):
        await runtime.type("hi", element_ref="split")
    result = await runtime.type("hi", element_ref="editor")
    assert result.success is True


async def test_type_reports_delivery_separately_from_effect_verification() -> None:
    stub = StubHelperClient(
        result={
            "characters": 3,
            "element_ref": "e1",
            "delivery_status": "delivered",
            "verification_status": "mismatch",
            "evidence": {"value_changed": False},
        }
    )
    result = await _runtime(stub).type("465")

    assert result.success is False
    assert result.verification_status is VerificationStatus.MISMATCH
    assert result.metadata["evidence"] == {"value_changed": False}


async def test_type_permission_error_propagates() -> None:
    stub = StubHelperClient(
        error=ComputerHelperError(
            "accessibility_permission_required: "
            "macOS Accessibility permission is required"
        )
    )
    runtime = _runtime(stub)

    with pytest.raises(ComputerHelperError, match="accessibility_permission_required"):
        await runtime.type("hi")


async def test_type_does_not_break_others() -> None:
    stub = StubHelperClient(
        per_method={
            "observe": _observe_result_with_elements(),
            "click_element": _click_result(),
            "type_text": _type_result(5),
            "open_app": {
                "app": "TextEdit",
                "bundle_id": "com.apple.TextEdit",
                "process_id": 5,
            },
        }
    )
    runtime = _runtime(stub)

    await runtime.observe()
    await runtime.click(ElementTarget(observation_id="obs-1", element_ref="e1"))
    await runtime.observe()
    type_result = await runtime.type("hello")
    await runtime.open_app("TextEdit")

    assert type_result.metadata["characters"] == 5
    assert [m for m, _ in stub.calls] == [
        "observe",
        "click_element",
        "observe",
        "type_text",
        "open_app",
    ]


# ---------------------------------------------------------------------------
# V6 key（CGEvent keyDown/keyUp）
# ---------------------------------------------------------------------------


def _key_result(
    key: str = "return",
    modifiers: list[str] | None = None,
) -> dict:
    return {"key": key, "modifiers": modifiers or []}


async def test_key_calls_key_press() -> None:
    stub = StubHelperClient(result=_key_result())
    runtime = _runtime(stub)

    result = await runtime.key("enter")

    assert stub.calls == [
        (
            "key_press",
            {"key": "enter", "modifiers": [], "expected_observation_id": "obs-current"},
        )
    ]
    assert result.success is True


async def test_key_passes_modifiers_and_converts_action_result() -> None:
    stub = StubHelperClient(result=_key_result("a", ["command", "shift"]))
    runtime = _runtime(stub)

    result = await runtime.key("a", ("cmd", "shift"))

    assert stub.calls == [
        (
            "key_press",
            {
                "key": "a",
                "modifiers": ["cmd", "shift"],
                "expected_observation_id": "obs-current",
            },
        )
    ]
    assert isinstance(result, ActionResult)
    assert result.success is True
    assert result.action is ActionName.KEY
    assert result.metadata == {
        "key": "a",
        "modifiers": ["command", "shift"],
    }


async def test_key_with_element_ref() -> None:
    stub = StubHelperClient(result=_key_result("enter", []))
    runtime = _runtime(stub)

    result = await runtime.key("enter", element_ref="e2")

    assert stub.calls == [
        (
            "key_press",
            {
                "key": "enter",
                "modifiers": [],
                "expected_observation_id": "obs-current",
                "element_ref": "e2",
            },
        )
    ]
    assert result.success is True


async def test_key_element_ref_validation() -> None:
    stub = StubHelperClient(result=_key_result())
    runtime = _runtime(stub)

    with pytest.raises(ValueError, match="element_ref"):
        await runtime.key("enter", element_ref=" ")  # type: ignore[arg-type]
    assert stub.calls == []


@pytest.mark.parametrize(
    "code",
    ["unsupported_key", "accessibility_permission_required"],
)
async def test_key_helper_error_propagates(code: str) -> None:
    stub = StubHelperClient(error=ComputerHelperError(f"{code}: test error"))
    runtime = _runtime(stub)

    with pytest.raises(ComputerHelperError, match=code):
        await runtime.key("enter")


@pytest.mark.parametrize("key", ["", "   ", None])
async def test_key_rejects_empty_or_non_string_key(key: object) -> None:
    stub = StubHelperClient(result=_key_result())
    runtime = _runtime(stub)

    with pytest.raises(ValueError, match="non-empty string"):
        await runtime.key(key)  # type: ignore[arg-type]
    assert stub.calls == []


@pytest.mark.parametrize(
    "modifiers",
    [["command"], ("command", 1)],
)
async def test_key_rejects_invalid_modifier_tuple(
    modifiers: object,
) -> None:
    stub = StubHelperClient(result=_key_result())
    runtime = _runtime(stub)

    with pytest.raises(ValueError, match="tuple of strings"):
        await runtime.key("a", modifiers)  # type: ignore[arg-type]
    assert stub.calls == []


async def test_key_does_not_break_existing_operations() -> None:
    stub = StubHelperClient(
        per_method={
            "observe": _observe_result_with_elements(),
            "click_element": _click_result(),
            "type_text": _type_result(5),
            "key_press": _key_result("tab"),
            "open_app": {
                "app": "TextEdit",
                "bundle_id": "com.apple.TextEdit",
                "process_id": 5,
            },
        }
    )
    runtime = _runtime(stub)

    await runtime.observe()
    await runtime.click(ElementTarget(observation_id="obs-1", element_ref="e1"))
    await runtime.observe()
    await runtime.type("hello")
    await runtime.observe()
    key_result = await runtime.key("tab")
    await runtime.open_app("TextEdit")

    assert key_result.action is ActionName.KEY
    assert [method for method, _ in stub.calls] == [
        "observe",
        "click_element",
        "observe",
        "type_text",
        "observe",
        "key_press",
        "open_app",
    ]


# ---------------------------------------------------------------------------
# 其它方法仍然 NotImplementedError
# ---------------------------------------------------------------------------


async def test_scroll_and_focus_window_are_implemented() -> None:
    stub = StubHelperClient(
        per_method={
            "observe": _observe_result_with_elements(),
            "scroll": {"delta_x": 0, "delta_y": -10},
            "focus_window": {"window_ref": "w1"},
        }
    )
    runtime = _runtime(stub)
    await runtime.observe(include_screenshot=False)
    focus = await runtime.focus_window("w1")
    assert focus.action is ActionName.FOCUS_WINDOW
    await runtime.observe(include_screenshot=False)
    scroll = await runtime.scroll(delta_y=-10)
    assert scroll.action is ActionName.SCROLL


async def test_focus_window_requires_latest_observation() -> None:
    runtime = _runtime(StubHelperClient())
    runtime._session_manager.require_active().invalidate_snapshot()
    with pytest.raises(ValueError, match="fresh observation required"):
        await runtime.focus_window("w1")


@pytest.mark.parametrize("action", ["type", "key", "scroll"])
async def test_input_actions_require_fresh_observation(action: str) -> None:
    runtime = _runtime(StubHelperClient(result={}))
    runtime._session_manager.require_active().invalidate_snapshot()
    with pytest.raises(ValueError, match="fresh observation required"):
        if action == "type":
            await runtime.type("hello")
        elif action == "key":
            await runtime.key("enter")
        else:
            await runtime.scroll(delta_y=-1)


async def test_observe_converts_multiple_windows_and_screenshot(tmp_path) -> None:
    payload = _observe_result_with_elements()
    payload.update(
        {
            "active_window_ref": "w2",
            "windows": [
                {
                    "ref": "w1",
                    "title": "First",
                    "bounds": {"x": 0, "y": 0, "width": 400, "height": 300},
                },
                {
                    "ref": "w2",
                    "title": "Focused",
                    "bounds": {"x": 10, "y": 20, "width": 800, "height": 600},
                },
            ],
            "screenshot_ref": str(tmp_path / "shot.png"),
        }
    )
    stub = StubHelperClient(result=payload)
    runtime = MacOSComputerRuntime(stub, screenshot_dir=tmp_path)  # type: ignore[arg-type]
    runtime.begin_session("test-run")
    observation = await runtime.observe()
    assert [window.ref for window in observation.windows] == ["w1", "w2"]
    assert observation.active_window is observation.windows[1]
    assert observation.screenshot_ref == str(tmp_path / "shot.png")
    params = stub.calls[0][1]
    assert params["include_screenshot"] is True
    assert str(params["screenshot_path"]).endswith(f"{observation.id}.png")


async def test_observe_without_screenshot_does_not_send_path(tmp_path) -> None:
    stub = StubHelperClient(result=_observe_result())
    runtime = MacOSComputerRuntime(stub, screenshot_dir=tmp_path)  # type: ignore[arg-type]
    runtime.begin_session("test-run")
    observation = await runtime.observe(include_screenshot=False)
    assert observation.screenshot_ref is None
    assert stub.calls[0][1]["include_screenshot"] is False
    assert "screenshot_path" not in stub.calls[0][1]


async def test_screenshot_error_keeps_structured_observation(tmp_path) -> None:
    payload = _observe_result()
    payload["screenshot_error"] = {"code": "screen_recording_permission_required"}
    runtime = MacOSComputerRuntime(  # type: ignore[arg-type]
        StubHelperClient(result=payload), screenshot_dir=tmp_path
    )
    runtime.begin_session("test-run")
    observation = await runtime.observe()
    assert observation.active_app is not None
    assert observation.active_window is not None
    assert observation.screenshot_ref is None


async def test_successful_mutations_invalidate_latest_observation(tmp_path) -> None:
    stub = StubHelperClient(
        per_method={
            "observe": _observe_result_with_elements(),
            "type_text": {"characters": 1},
            "key_press": {"key": "enter", "modifiers": []},
            "open_app": {},
            "scroll": {"delta_x": 0, "delta_y": 1},
        }
    )
    runtime = MacOSComputerRuntime(stub, screenshot_dir=tmp_path)  # type: ignore[arg-type]
    runtime.begin_session("test-run")
    await runtime.observe(False)
    assert _snapshot(runtime) is not None
    await runtime.type("")
    assert _snapshot(runtime) is not None
    await runtime.type("x")
    assert _snapshot(runtime) is None

    for mutation in (
        runtime.key("enter"),
        runtime.scroll(delta_y=1),
        runtime.open_app("TextEdit"),
    ):
        await runtime.observe(False)
        await mutation
        assert _snapshot(runtime) is None


@pytest.mark.parametrize(
    "code",
    ["stale_observation", "screenshot_unavailable", "coordinate_out_of_bounds"],
)
async def test_coordinate_click_errors_propagate_and_keep_cache(code) -> None:
    runtime = _runtime(StubHelperClient(error=ComputerHelperError(code)))
    _attach(runtime, Observation(id="obs-1"))
    with pytest.raises(ComputerHelperError, match=code):
        await runtime.click(CoordinateTarget(observation_id="obs-1", x=1, y=2))
    expected = None if code == "stale_observation" else "obs-1"
    current = _snapshot(runtime)
    assert (current.id if current else None) == expected


async def test_scroll_error_propagates_and_keeps_cache() -> None:
    runtime = _runtime(
        StubHelperClient(error=ComputerHelperError("input_event_failed"))
    )
    _attach(runtime, Observation(id="obs-1"))
    with pytest.raises(ComputerHelperError, match="input_event_failed"):
        await runtime.scroll(delta_y=-10)
    current = _snapshot(runtime)
    assert (current.id if current else None) == "obs-1"


async def test_uncertain_mutation_failure_invalidates_without_retry() -> None:
    stub = StubHelperClient(error=ComputerHelperProcessError("helper exited"))
    runtime = _runtime(stub)
    with pytest.raises(ComputerHelperProcessError):
        await runtime.type("hello")
    assert len(stub.calls) == 1
    assert _snapshot(runtime) is None
