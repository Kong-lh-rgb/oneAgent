"""Computer Runtime V0：屏幕观察 / 交互目标的纯数据契约与假实现。

本轮提供：
- ``models``：Observation / ElementTarget / CoordinateTarget / ActionResult；
- ``runtime``：ComputerRuntime 异步接口（Protocol）；
- ``fake``：FakeComputerRuntime（供未来 Agent Tool 单测使用）；
- ``tools``：computer_observe / click / type / key / scroll / open_app /
  focus_window 七个 Agent 工具 + ``register_computer_tools``。

不包含任何真实 macOS 实现（AXUIElement / ScreenCaptureKit / CGEvent）。
"""

from .fake import FakeComputerRuntime, default_observation
from .models import (
    ActionName,
    ActionResult,
    ActiveApp,
    Bounds,
    CoordinateTarget,
    Element,
    ElementTarget,
    Observation,
    Target,
    Window,
)
from .runtime import ComputerRuntime
from .tools import (
    ComputerClickTool,
    ComputerFocusWindowTool,
    ComputerKeyTool,
    ComputerObserveTool,
    ComputerOpenAppTool,
    ComputerScrollTool,
    ComputerTypeTool,
    register_computer_tools,
)

__all__ = [
    "ActionName",
    "ActionResult",
    "ActiveApp",
    "Bounds",
    "ComputerClickTool",
    "ComputerFocusWindowTool",
    "ComputerKeyTool",
    "ComputerObserveTool",
    "ComputerOpenAppTool",
    "ComputerRuntime",
    "ComputerScrollTool",
    "ComputerTypeTool",
    "CoordinateTarget",
    "Element",
    "ElementTarget",
    "FakeComputerRuntime",
    "Observation",
    "Target",
    "Window",
    "default_observation",
    "register_computer_tools",
]
