"""MacOSComputerRuntime：真实 macOS ComputerRuntime（V6）。

V5 实现 ``open_app``（NSWorkspace 打开应用）、``observe``（读取当前前台
App / 窗口及其可交互 AX UI 元素）、``click``（ElementTarget → AXPress）与
``type``（CGEvent Unicode 文本输入到当前焦点）与 ``key``（CGEvent
keyDown/keyUp），其余契约方法（scroll/focus_window）仍抛
``NotImplementedError``，不返回假的成功。

真实电脑控制（完整 AX Tree / ScreenCaptureKit / CGEvent 其余能力）留到后续
轮次；FakeComputerRuntime 继续用于 Agent Tool 测试。
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
    "macOS Computer Runtime 当前只实现 open_app / observe / click / type / key"
    "（AXPress / CGEvent 键盘输入），该操作尚未实现真实电脑控制"
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
        """语义点击（V4）：只支持 ElementTarget → Swift AXPress。

        通过 ``click_element`` 让 Swift 对当前缓存中对应的 AXUIElement
        执行 AXPress（不模拟鼠标、不做坐标点击）。成功后 Swift 会使旧
        Observation 失效（element ref 不再可信，Agent 需重新 observe）。

        ``CoordinateTarget`` 本轮明确拒绝：ScreenCapture / Retina 坐标
        映射尚未实现，不能把截图坐标当 macOS 全局坐标。

        helper 返回 error（stale_observation / element_not_found /
        action_not_supported / ax_action_failed）时向上抛出
        ``ComputerHelperError``。
        """

        if isinstance(target, ElementTarget):
            result = await self.helper_client.call(
                "click_element",
                {
                    "observation_id": target.observation_id,
                    "element_ref": target.element_ref,
                },
            )
            return ActionResult(
                success=True,
                action=ActionName.CLICK,
                observation_id=target.observation_id,
                metadata={
                    "element_ref": target.element_ref,
                    "method": "ax_press",
                    "action": result.get("action"),
                },
            )

        if isinstance(target, CoordinateTarget):
            raise NotImplementedError(
                "coordinate click is not implemented yet"
            )

        raise ValueError("unsupported click target")

    async def type(self, text: str) -> ActionResult:
        """向当前 macOS keyboard focus 输入文本（V5，CGEvent Unicode）。

        通过 ``type_text`` 让 Swift 用 CGEvent keyboardSetUnicodeString 把
        Unicode 文本输入到当前焦点位置（非 clipboard / Cmd+V / osascript）。
        只面向"当前焦点"：不接受 element_ref / observation_id，也不会自动
        找文本框。

        不在 metadata 中保存完整 text（避免复制长/敏感内容）。
        """

        if not isinstance(text, str):
            raise ValueError("'text' must be a string")

        result = await self.helper_client.call("type_text", {"text": text})
        return ActionResult(
            success=True,
            action=ActionName.TYPE,
            metadata={
                "characters": result.get("characters", 0),
            },
        )

    async def key(
        self,
        key: str,
        modifiers: tuple[str, ...] = (),
    ) -> ActionResult:
        """发送一个物理按键语义的 CGEvent keyDown/keyUp（V6）。

        ``computer_type`` 负责 Unicode 文本，``computer_key`` 只负责明确
        键位与快捷键。键位、modifier 支持范围和规范化由 Swift helper
        统一处理；helper 的 unsupported_key / invalid_modifier / 权限错误
        继续以 ``ComputerHelperError`` 向上传播。
        """

        if not isinstance(key, str) or not key.strip():
            raise ValueError("'key' must be a non-empty string")
        if not isinstance(modifiers, tuple) or not all(
            isinstance(modifier, str) for modifier in modifiers
        ):
            raise ValueError("'modifiers' must be a tuple of strings")

        result = await self.helper_client.call(
            "key_press",
            {
                "key": key,
                "modifiers": list(modifiers),
            },
        )
        return ActionResult(
            success=True,
            action=ActionName.KEY,
            metadata={
                "key": result.get("key"),
                "modifiers": result.get("modifiers", []),
            },
        )

    async def scroll(
        self,
        delta_x: int = 0,
        delta_y: int = 0,
    ) -> ActionResult:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    async def focus_window(self, window_ref: str) -> ActionResult:
        raise NotImplementedError(_NOT_IMPLEMENTED)
