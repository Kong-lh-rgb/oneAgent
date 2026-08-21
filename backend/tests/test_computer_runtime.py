"""Computer Runtime V0 契约单元测试。

全部为纯单元测试：不调用真实 macOS API、不需要 Accessibility /
Screen Recording 权限、不调用模型 API。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.computer import (
    ActionName,
    ActionResult,
    ActiveApp,
    Bounds,
    ComputerRuntime,
    CoordinateTarget,
    Element,
    ElementTarget,
    FakeComputerRuntime,
    Observation,
    Window,
    default_observation,
)

# ---------------------------------------------------------------------------
# 1. Observation / Target 正常构造
# ---------------------------------------------------------------------------


def test_observation_constructs_with_snapshot_fields() -> None:
    obs = Observation(
        active_app=ActiveApp(name="Notes", bundle_id="com.apple.Notes", pid=123),
        active_window=Window(
            ref="w1",
            title="Todo",
            bounds=Bounds(x=0, y=0, width=800, height=600),
        ),
        windows=(
            Window(
                ref="w1",
                title="Todo",
                bounds=Bounds(x=0, y=0, width=800, height=600),
            ),
            Window(
                ref="w2",
                title="Safari",
                bounds=Bounds(x=10, y=10, width=400, height=300),
            ),
        ),
        elements=(
            Element(
                ref="e1",
                role="button",
                title="Add",
                enabled=True,
                focused=True,
                bounds=Bounds(x=5, y=5, width=60, height=24),
                actions=("press",),
            ),
            Element(ref="e2", role="text_field", value="", focused=False),
        ),
        screenshot_ref="shot-1",
    )

    assert obs.id
    assert obs.active_app is not None
    assert obs.active_app.name == "Notes"
    assert obs.active_app.pid == 123
    assert obs.active_window is not None
    assert obs.active_window.title == "Todo"
    assert len(obs.windows) == 2
    assert len(obs.elements) == 2
    assert obs.elements[0].actions == ("press",)
    assert obs.screenshot_ref == "shot-1"


def test_element_target_constructs() -> None:
    target = ElementTarget(observation_id="obs-1", element_ref="e1")
    assert target.observation_id == "obs-1"
    assert target.element_ref == "e1"


def test_coordinate_target_constructs() -> None:
    target = CoordinateTarget(observation_id="obs-1", x=120, y=240)
    assert target.observation_id == "obs-1"
    assert target.x == 120
    assert target.y == 240


# ---------------------------------------------------------------------------
# 基础校验
# ---------------------------------------------------------------------------


def test_observation_id_cannot_be_empty() -> None:
    with pytest.raises(ValidationError):
        Observation(id="   ")


def test_element_ref_cannot_be_empty() -> None:
    with pytest.raises(ValidationError):
        Element(ref="")


def test_element_target_requires_observation_id() -> None:
    # 不允许只有 element_ref —— element ref 只在对应 Observation 内有效。
    with pytest.raises(ValidationError):
        ElementTarget(element_ref="e1")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        ElementTarget(observation_id="", element_ref="e1")


def test_window_ref_cannot_be_empty() -> None:
    with pytest.raises(ValidationError):
        Window(ref="  ", bounds=Bounds(x=0, y=0, width=10, height=10))


def test_invalid_bounds_rejected() -> None:
    # 宽度 / 高度不能为负数。
    with pytest.raises(ValidationError, match="cannot be negative"):
        Bounds(x=0, y=0, width=-1, height=10)
    with pytest.raises(ValidationError, match="cannot be negative"):
        Bounds(x=0, y=0, width=10, height=-1)
    # 坐标为整数；允许负数不是问题，尺寸为负才是问题。
    assert Bounds(x=-5, y=0, width=10, height=10).x == -5


def test_invalid_app_pid_rejected() -> None:
    with pytest.raises(ValidationError, match="positive"):
        ActiveApp(name="Notes", pid=0)
    with pytest.raises(ValidationError, match="positive"):
        ActiveApp(name="Notes", pid=-1)
    # pid 缺省合法。
    assert ActiveApp(name="Notes").pid is None


def test_action_names_are_stable() -> None:
    assert [name.value for name in ActionName] == [
        "click",
        "type",
        "key",
        "scroll",
        "open_app",
        "focus_window",
    ]


# ---------------------------------------------------------------------------
# FakeComputerRuntime
# ---------------------------------------------------------------------------


async def test_fake_observe_returns_preset_observation() -> None:
    obs = Observation(id="obs-fixed")
    fake = FakeComputerRuntime(observation=obs)
    observed = await fake.observe()
    assert observed is obs
    assert observed.id == "obs-fixed"


async def test_fake_observe_default_observation() -> None:
    fake = FakeComputerRuntime()
    observed = await fake.observe()
    assert observed.id
    assert observed.active_app is not None
    assert observed.active_window is not None
    assert observed.elements


async def test_fake_click_element_target() -> None:
    obs = default_observation()
    fake = FakeComputerRuntime(observation=obs)
    result = await fake.click(
        ElementTarget(observation_id=obs.id, element_ref="e1")
    )
    assert isinstance(result, ActionResult)
    assert result.success is True
    assert result.action is ActionName.CLICK
    assert result.observation_id == obs.id
    assert result.metadata["element_ref"] == "e1"


async def test_fake_click_coordinate_target() -> None:
    obs = default_observation()
    fake = FakeComputerRuntime(observation=obs)
    result = await fake.click(CoordinateTarget(observation_id=obs.id, x=10, y=20))
    assert result.success is True
    assert result.action is ActionName.CLICK
    assert result.metadata["x"] == 10
    assert result.metadata["y"] == 20


async def test_fake_type() -> None:
    fake = FakeComputerRuntime()
    result = await fake.type("hello")
    assert result.action is ActionName.TYPE
    assert result.success is True
    assert result.metadata["text"] == "hello"

    with_ref = await fake.type("hi", element_ref="e2")
    assert with_ref.metadata["element_ref"] == "e2"


async def test_fake_key() -> None:
    fake = FakeComputerRuntime()
    result = await fake.key("enter")
    assert result.action is ActionName.KEY
    assert result.metadata["key"] == "enter"
    assert result.metadata["modifiers"] == ()

    with_mods = await fake.key("a", modifiers=("command", "shift"))
    assert with_mods.metadata["modifiers"] == ("command", "shift")

    with_ref = await fake.key("enter", element_ref="e2")
    assert with_ref.metadata["element_ref"] == "e2"


async def test_fake_scroll() -> None:
    fake = FakeComputerRuntime()
    result = await fake.scroll(delta_y=3)
    assert result.action is ActionName.SCROLL
    assert result.metadata["delta_x"] == 0
    assert result.metadata["delta_y"] == 3


async def test_fake_open_app() -> None:
    fake = FakeComputerRuntime()
    result = await fake.open_app("Notes")
    assert result.action is ActionName.OPEN_APP
    assert result.metadata["app"] == "Notes"


async def test_fake_focus_window() -> None:
    fake = FakeComputerRuntime()
    result = await fake.focus_window("w1")
    assert result.action is ActionName.FOCUS_WINDOW
    assert result.metadata["window_ref"] == "w1"


async def test_fake_action_history_order() -> None:
    fake = FakeComputerRuntime(observation=default_observation())
    obs = fake.observation

    await fake.open_app("Notes")
    await fake.click(ElementTarget(observation_id=obs.id, element_ref="e1"))
    await fake.type("hi")
    await fake.key("enter")
    await fake.scroll(delta_y=-2)
    await fake.focus_window("w2")

    assert [item.action for item in fake.action_history] == [
        ActionName.OPEN_APP,
        ActionName.CLICK,
        ActionName.TYPE,
        ActionName.KEY,
        ActionName.SCROLL,
        ActionName.FOCUS_WINDOW,
    ]
    # 顺序即执行顺序；每次返回的也是同一批记录。
    assert fake.action_history[1].observation_id == obs.id


async def test_fake_implements_computer_runtime_protocol() -> None:
    fake = FakeComputerRuntime()

    def accept(runtime: ComputerRuntime) -> ComputerRuntime:
        return runtime

    runtime = accept(fake)
    observation = await runtime.observe()
    result = await runtime.click(
        ElementTarget(observation_id=observation.id, element_ref="e1")
    )
    assert result.success is True
