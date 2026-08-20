"""MacOSComputerRuntime：真实 macOS ComputerRuntime（V3）。

V3 实现 ``open_app``（NSWorkspace 打开应用）与 ``observe``（读取当前前台
App / 窗口及其可交互 AX UI 元素），其余契约方法（click/type/key/scroll/
focus_window）仍抛 ``NotImplementedError``，不返回假的成功。

真实电脑控制（完整 AX Tree / ScreenCaptureKit / CGEvent 等）留到后续轮次；
FakeComputerRuntime 继续用于 Agent Tool 测试。
"""

from __future__ import annotations

import uuid

from .helper_client import MacOSHelperClient
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

__all__ = ["MacOSComputerRuntime"]

_NOT_IMPLEMENTED = (
    "macOS Computer Runtime 本轮只实现 open_app / observe，"
    "该操作尚未实现真实电脑控制"
)


def _element_from_dict(data: dict) -> Element:
    """把 Swift helper 返回的 element dict 转成现有 Element 模型。"""

    bounds_data = data.get("bounds")
    bounds: Bounds | None = None
    if isinstance(bounds_data, dict):
        bounds = Bounds(
            x=bounds_data.get("x", 0),
            y=bounds_data.get("y", 0),
            width=bounds_data.get("width", 0),
            height=bounds_data.get("height", 0),
        )
    actions = data.get("actions") or ()
    return Element(
        ref=data.get("ref") or "",
        role=data.get("role") or "",
        title=data.get("title"),
        value=data.get("value"),
        enabled=data.get("enabled", True),
        focused=data.get("focused", False),
        bounds=bounds,
        actions=tuple(actions),
    )


class MacOSComputerRuntime:
    """把真实操作委托给 Swift helper（当前实现 open_app / observe）。"""

    def __init__(self, helper_client: MacOSHelperClient) -> None:
        self.helper_client = helper_client

    # ------------------------------------------------------------------
    # 生命周期（Application 只在注入真实 MacOSComputerRuntime 时调用）
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """启动 Swift helper 长驻子进程。"""

        await self.helper_client.start()

    async def close(self) -> None:
        """关闭 helper 子进程（幂等）。"""

        await self.helper_client.close()

    # ------------------------------------------------------------------
    # ComputerRuntime 契约
    # ------------------------------------------------------------------

    async def open_app(self, app: str) -> ActionResult:
        """通过 Swift helper（NSWorkspace）真正打开一个应用。

        ``app`` 支持应用名称或 bundle id。helper 返回 error（如
        app_not_found / app_launch_failed）时向上抛出 ComputerHelperError。
        """

        if not isinstance(app, str) or not app.strip():
            raise ValueError("'app' must be a non-empty string")

        result = await self.helper_client.call("open_app", {"app": app})
        return ActionResult(
            success=True,
            action=ActionName.OPEN_APP,
            metadata={
                "app": result.get("app"),
                "bundle_id": result.get("bundle_id"),
                "process_id": result.get("process_id"),
            },
        )

    async def observe(self, include_screenshot: bool = True) -> Observation:
        """获取当前前台 App / 窗口及其可交互 UI 元素（V3）。

        调用 Swift helper 的 ``observe``：前台 App 来自
        NSWorkspace.frontmostApplication，窗口与元素来自 AXUIElement
        （AXFocusedWindow + AXChildren 递归遍历，role / value 已
        normalize，element ref 只在当前 observation_id 内有效）。

        V3 只提供 structured observation，**不做截图**：
        ``include_screenshot`` 当前被忽略，``screenshot_ref`` 恒为 None
        （ScreenCaptureKit 留到下一阶段），不抛错以保持
        computer_observe 默认参数可用。

        helper 未授权 Accessibility 时抛出
        ``ComputerHelperError("accessibility_permission_required: ...")``。
        """

        observation_id = uuid.uuid4().hex
        result = await self.helper_client.call(
            "observe", {"observation_id": observation_id}
        )

        active_app: ActiveApp | None = None
        app_data = result.get("active_app")
        if isinstance(app_data, dict):
            active_app = ActiveApp(
                name=app_data.get("name") or "unknown",
                bundle_id=app_data.get("bundle_id"),
                pid=app_data.get("process_id"),
            )

        active_window: Window | None = None
        window_data = result.get("active_window")
        if isinstance(window_data, dict):
            bounds = window_data.get("bounds") or {}
            active_window = Window(
                # V3 只返回 active window，ref 用当前 observation 内的临时值。
                ref="w1",
                title=window_data.get("title", ""),
                bounds=Bounds(
                    x=bounds.get("x", 0),
                    y=bounds.get("y", 0),
                    width=bounds.get("width", 0),
                    height=bounds.get("height", 0),
                ),
            )

        elements_data = result.get("elements") or []
        elements = tuple(
            _element_from_dict(data)
            for data in elements_data
            if isinstance(data, dict)
        )

        return Observation(
            id=observation_id,
            active_app=active_app,
            active_window=active_window,
            windows=(active_window,) if active_window is not None else (),
            elements=elements,
            screenshot_ref=None,
        )

    async def click(
        self,
        target: ElementTarget | CoordinateTarget,
    ) -> ActionResult:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    async def type(self, text: str) -> ActionResult:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    async def key(
        self,
        key: str,
        modifiers: tuple[str, ...] = (),
    ) -> ActionResult:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    async def scroll(
        self,
        delta_x: int = 0,
        delta_y: int = 0,
    ) -> ActionResult:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    async def focus_window(self, window_ref: str) -> ActionResult:
        raise NotImplementedError(_NOT_IMPLEMENTED)
