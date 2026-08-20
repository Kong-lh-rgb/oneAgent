"""MacOSComputerRuntime：真实 macOS ComputerRuntime（V1）。

V1 只实现 ``open_app``（通过 Swift helper → NSWorkspace 真正打开应用），
其余契约方法（observe/click/type/key/scroll/focus_window）仍抛
``NotImplementedError``，不返回假的成功。

真实电脑控制（AXUIElement / ScreenCaptureKit / CGEvent / NSWorkspace 其余
能力）留到后续轮次；FakeComputerRuntime 继续用于 Agent Tool 测试。
"""

from __future__ import annotations

from .helper_client import MacOSHelperClient
from .models import (
    ActionName,
    ActionResult,
    CoordinateTarget,
    ElementTarget,
    Observation,
)

__all__ = ["MacOSComputerRuntime"]

_NOT_IMPLEMENTED = (
    "macOS Computer Runtime 本轮只实现 open_app，"
    "该操作尚未实现真实电脑控制"
)


class MacOSComputerRuntime:
    """把真实操作委托给 Swift helper 的骨架（当前仅 open_app）。"""

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

    async def focus_window(self, window_ref: str) -> ActionResult:
        raise NotImplementedError(_NOT_IMPLEMENTED)
