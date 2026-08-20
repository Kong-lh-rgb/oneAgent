"""MacOSComputerRuntime：真实 macOS ComputerRuntime 的骨架（V0）。

本轮只建立生命周期（start / close 驱动 MacOSHelperClient 的长驻子进程），
ComputerRuntime 契约里的 7 个真实操作方法（observe/click/type/key/scroll/
open_app/focus_window）**全部**抛 ``NotImplementedError``，不返回假的成功。

真实电脑控制（AXUIElement / ScreenCaptureKit / CGEvent / NSWorkspace）
留到后续轮次；FakeComputerRuntime 继续用于 Agent Tool 测试。
"""

from __future__ import annotations

from .helper_client import MacOSHelperClient
from .models import ActionResult, CoordinateTarget, ElementTarget, Observation

__all__ = ["MacOSComputerRuntime"]

_NOT_IMPLEMENTED = (
    "macOS Computer Runtime 本轮只打通 Helper 生命周期，"
    "该操作尚未实现真实电脑控制"
)


class MacOSComputerRuntime:
    """把真实操作委托给 Swift helper 的骨架（当前仅生命周期）。"""

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
    # ComputerRuntime 契约 —— V0 全部未实现
    # ------------------------------------------------------------------

    async def observe(self, include_screenshot: bool = True) -> Observation:
        raise NotImplementedError(_NOT_IMPLEMENTED)

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

    async def open_app(self, app: str) -> ActionResult:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    async def focus_window(self, window_ref: str) -> ActionResult:
        raise NotImplementedError(_NOT_IMPLEMENTED)
