"""FakeComputerRuntime：用于 Agent Tool 单测的假实现。

- 可预设 Observation（不传则给一个默认观察）；
- 每个 action（click/type/key/scroll/open_app/focus_window）会返回
  ActionResult 并记录进 ``action_history``；
- ``observe`` 直接返回预设 Observation，不模拟真实 GUI 状态变化。

保持简单：不模拟焦点、不模拟状态机、不需要任何 macOS / Accessibility 权限。
"""

from __future__ import annotations

from typing import Any

from .models import (
    ActionName,
    ActionResult,
    ActiveApp,
    Bounds,
    CoordinateTarget,
    Element,
    ElementTarget,
    Observation,
    Window,
)


def default_observation() -> Observation:
    """构造一个用于测试的默认观察快照。"""

    return Observation(
        active_app=ActiveApp(
            name="FakeApp",
            bundle_id="com.example.fake",
            pid=4242,
        ),
        active_window=Window(
            ref="w1",
            title="Fake Window",
            bounds=Bounds(x=0, y=0, width=800, height=600),
        ),
        windows=(
            Window(
                ref="w1",
                title="Fake Window",
                bounds=Bounds(x=0, y=0, width=800, height=600),
            ),
            Window(
                ref="w2",
                title="Second Window",
                bounds=Bounds(x=100, y=100, width=400, height=300),
            ),
        ),
        elements=(
            Element(
                ref="e1",
                role="button",
                title="OK",
                enabled=True,
                bounds=Bounds(x=10, y=10, width=80, height=30),
            ),
            Element(
                ref="e2",
                role="text_field",
                value="hello",
                focused=True,
                bounds=Bounds(x=10, y=50, width=200, height=24),
            ),
        ),
        screenshot_ref="screenshot-1",
    )


class FakeComputerRuntime:
    """预设 Observation 并记录 action_history 的假 Computer Runtime。"""

    def __init__(self, observation: Observation | None = None) -> None:
        self._observation = observation or default_observation()
        self.action_history: list[ActionResult] = []

    @property
    def observation(self) -> Observation:
        """当前预设的 Observation（observe 返回的就是它）。"""

        return self._observation

    async def observe(self, include_screenshot: bool = True) -> Observation:
        return self._observation

    async def click(
        self,
        target: ElementTarget | CoordinateTarget,
    ) -> ActionResult:
        metadata: dict[str, Any] = {}
        if isinstance(target, ElementTarget):
            metadata["element_ref"] = target.element_ref
        else:
            metadata["x"] = target.x
            metadata["y"] = target.y
        return self._record(
            ActionName.CLICK,
            observation_id=target.observation_id,
            metadata=metadata,
        )

    async def type(self, text: str) -> ActionResult:
        return self._record(
            ActionName.TYPE,
            metadata={"text": text},
        )

    async def key(
        self,
        key: str,
        modifiers: tuple[str, ...] = (),
    ) -> ActionResult:
        return self._record(
            ActionName.KEY,
            metadata={"key": key, "modifiers": modifiers},
        )

    async def scroll(
        self,
        delta_x: int = 0,
        delta_y: int = 0,
    ) -> ActionResult:
        return self._record(
            ActionName.SCROLL,
            metadata={"delta_x": delta_x, "delta_y": delta_y},
        )

    async def open_app(self, app: str) -> ActionResult:
        return self._record(
            ActionName.OPEN_APP,
            metadata={"app": app},
        )

    async def focus_window(self, window_ref: str) -> ActionResult:
        return self._record(
            ActionName.FOCUS_WINDOW,
            metadata={"window_ref": window_ref},
        )

    def _record(
        self,
        action: ActionName,
        *,
        observation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ActionResult:
        result = ActionResult(
            success=True,
            action=action,
            observation_id=observation_id,
            metadata=metadata or {},
        )
        self.action_history.append(result)
        return result


__all__ = ["FakeComputerRuntime", "default_observation"]
