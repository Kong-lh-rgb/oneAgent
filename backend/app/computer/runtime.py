"""ComputerRuntime 异步接口（V0 Core Contract）。

只定义契约，不实现任何 macOS 逻辑（AXUIElement / ScreenCaptureKit /
CGEvent / NSWorkspace 均不在本模块）。

接口语义：
- ``observe``：返回一次结构化 Observation；
- ``click``：对 ElementTarget / CoordinateTarget 执行点击；
- ``type``：在当前焦点处输入文本；
- ``key``：发送按键（可带修饰键，如 ("command", "shift")）；
- ``scroll``：滚动（delta_x / delta_y）；
- ``open_app``：打开应用；
- ``focus_window``：聚焦某个窗口（window_ref 来自 Observation.windows）。
"""

from __future__ import annotations

from typing import Protocol

from .models import ActionResult, CoordinateTarget, ElementTarget, Observation

__all__ = ["ComputerRuntime"]


class ComputerRuntime(Protocol):
    """Computer Runtime 的异步契约（不携带任何实现细节）。"""

    async def observe(self, include_screenshot: bool = True) -> Observation:
        """抓取一次屏幕观察。

        ``include_screenshot``：是否生成截图引用（V0 不保存截图文件，
        只预留 screenshot_ref 语义）。
        """
        ...

    async def click(
        self,
        target: ElementTarget | CoordinateTarget,
    ) -> ActionResult:
        """对元素或截图坐标执行点击。"""
        ...

    async def type(
        self,
        text: str,
        element_ref: str | None = None,
    ) -> ActionResult:
        """在当前焦点输入文本。

        ``element_ref`` 可选：指定本次 Observation 中的元素 ref 时，
        先聚焦该元素（如编辑器 text_area）再输入，避免打进错误位置。
        """
        ...

    async def key(
        self,
        key: str,
        modifiers: tuple[str, ...] = (),
        element_ref: str | None = None,
    ) -> ActionResult:
        """发送按键，可带修饰键；可选先聚焦指定元素再发送。"""
        ...

    async def scroll(
        self,
        delta_x: int = 0,
        delta_y: int = 0,
    ) -> ActionResult:
        """滚动。"""
        ...

    async def open_app(self, app: str) -> ActionResult:
        """打开应用。"""
        ...

    async def focus_window(self, window_ref: str) -> ActionResult:
        """聚焦窗口。"""
        ...
