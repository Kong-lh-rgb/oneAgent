"""Computer Runtime V0 工具层：把 ComputerRuntime 契约暴露给 Agent。

所有工具只做三件事：
- 定义 ToolDefinition（含参数 schema 与权限档位）；
- 把 JSON 参数解析成 ``computer.models`` 的 Target / 参数；
- 调用注入的 ComputerRuntime（V0 用 FakeComputerRuntime，不操作系统）。

不直接操作系统、不 import pyautogui、不写任何 macOS API。ComputerRuntime
必须由 Application composition root 注入，Tool 不自建 Runtime。

权限（复用现有 ToolPermission）：
- computer_observe / computer_open_app / computer_focus_window /
  computer_scroll → ALLOWED（低风险）；
- computer_click / computer_type / computer_key → HUMAN_APPROVAL（需人工审批）。
"""

from __future__ import annotations

import json
from typing import Any

from app.models.types import ToolDefinition, ToolPermission, ToolUiScope

from ..tools.base import BaseTool
from ..tools.registry import ToolRegistry
from .models import CoordinateTarget, ElementTarget
from .runtime import ComputerRuntime


class ComputerObserveTool(BaseTool):
    """抓取一次屏幕观察。先观察，再操作。"""

    def __init__(self, runtime: ComputerRuntime) -> None:
        self._runtime = runtime

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="computer_observe",
            description=(
                "抓取一次屏幕观察（活动应用 / 窗口 / UI 元素树 / 截图引用），"
                "返回结构化 Observation。任何 computer_* 操作之前必须先调用本工具"
                "获得 observation_id；不要盲目 click。元素引用只在本次 Observation"
                "内有效。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "include_screenshot": {
                        "type": "boolean",
                        "description": "是否生成截图引用，默认 true。",
                    },
                },
                "additionalProperties": False,
            },
            permission=ToolPermission.ALLOWED,
            ui_scope=ToolUiScope.DESKTOP,
            strict=False,
        )

    async def execute(self, arguments: dict[str, Any]) -> Any:
        return await self._execute(arguments)

    async def execute_with_context(
        self,
        arguments: dict[str, Any],
        context: Any,
    ) -> Any:
        return await self._execute(arguments)

    async def _execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        include_screenshot = arguments.get("include_screenshot", True)
        if not isinstance(include_screenshot, bool):
            raise ValueError("'include_screenshot' must be a boolean")
        observation = await self._runtime.observe(
            include_screenshot=include_screenshot,
        )
        return _bounded_observation_payload(observation.model_dump(mode="json"))


class ComputerClickTool(BaseTool):
    """对元素或截图坐标执行点击（HUMAN_APPROVAL）。"""

    def __init__(self, runtime: ComputerRuntime) -> None:
        self._runtime = runtime

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="computer_click",
            description=(
                "对某次 Observation 中的元素（element_ref）或截图坐标（x/y）执行点击。"
                "必须先 computer_observe 拿到 observation_id。二选一：提供 element_ref "
                "或同时提供 x/y，不要同时提供两者。坐标是 observation 截图坐标系。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "observation_id": {
                        "type": "string",
                        "description": "目标所属 Observation 的 ID。",
                    },
                    "element_ref": {
                        "type": "string",
                        "description": "元素引用（元素目标）。",
                    },
                    "x": {
                        "type": "integer",
                        "description": "截图坐标 X（坐标目标，需与 y 一起）。",
                    },
                    "y": {
                        "type": "integer",
                        "description": "截图坐标 Y（坐标目标，需与 x 一起）。",
                    },
                },
                "required": ["observation_id"],
                "additionalProperties": False,
            },
            permission=ToolPermission.HUMAN_APPROVAL,
            ui_scope=ToolUiScope.DESKTOP,
            strict=False,
        )

    async def execute(self, arguments: dict[str, Any]) -> Any:
        return await self._execute(arguments)

    async def execute_with_context(
        self,
        arguments: dict[str, Any],
        context: Any,
    ) -> Any:
        return await self._execute(arguments)

    async def _execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        target = _parse_click_target(arguments)
        result = await self._runtime.click(target)
        return result.model_dump(mode="json")


class ComputerTypeTool(BaseTool):
    """向明确的可编辑元素输入文本（HUMAN_APPROVAL）。"""

    def __init__(self, runtime: ComputerRuntime) -> None:
        self._runtime = runtime

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="computer_type",
            description=(
                "向最近一次 Observation 中明确的可编辑元素输入文本，不操作剪贴板。"
                "优先提供 element_ref；省略时只会自动使用唯一的 focused+editable "
                "元素，否则安全拒绝。返回 delivery_status 和 verification_status；"
                "unverified 表示事件已投递但仍须再次 observe，不能宣称界面已完成。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "要输入的文本。",
                    },
                    "element_ref": {
                        "type": "string",
                        "description": (
                            "建议提供：来自最近 Observation 的 editable 元素 ref。"
                        ),
                    },
                },
                "required": ["text"],
                "additionalProperties": False,
            },
            permission=ToolPermission.HUMAN_APPROVAL,
            ui_scope=ToolUiScope.DESKTOP,
            strict=False,
        )

    async def execute(self, arguments: dict[str, Any]) -> Any:
        return await self._execute(arguments)

    async def execute_with_context(
        self,
        arguments: dict[str, Any],
        context: Any,
    ) -> Any:
        return await self._execute(arguments)

    async def _execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        text = arguments.get("text")
        if not isinstance(text, str):
            raise ValueError("'text' must be a string")
        element_ref = _optional_element_ref(arguments.get("element_ref"))
        result = await self._runtime.type(text, element_ref=element_ref)
        if not result.success:
            raise RuntimeError(
                "input_effect_mismatch: event was delivered but the editable "
                "element value did not change"
            )
        return result.model_dump(mode="json")


class ComputerKeyTool(BaseTool):
    """发送按键（HUMAN_APPROVAL）。"""

    def __init__(self, runtime: ComputerRuntime) -> None:
        self._runtime = runtime

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="computer_key",
            description=(
                "发送一个按键，可带修饰键（如 command/shift/option/control）。"
                "keycode 转换由 macOS runtime/helper 负责。"
                "可选 element_ref：先聚焦该元素（如编辑器）再发送按键。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "按键名，如 enter / tab / c。",
                    },
                    "modifiers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "修饰键列表，默认空。",
                    },
                    "element_ref": {
                        "type": "string",
                        "description": (
                            "可选：先聚焦该元素再发送按键"
                            "（来自本次 Observation）。"
                        ),
                    },
                },
                "required": ["key"],
                "additionalProperties": False,
            },
            permission=ToolPermission.HUMAN_APPROVAL,
            ui_scope=ToolUiScope.DESKTOP,
            strict=False,
        )

    async def execute(self, arguments: dict[str, Any]) -> Any:
        return await self._execute(arguments)

    async def execute_with_context(
        self,
        arguments: dict[str, Any],
        context: Any,
    ) -> Any:
        return await self._execute(arguments)

    async def _execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        key = arguments.get("key")
        if not isinstance(key, str) or not key.strip():
            raise ValueError("'key' must be a non-empty string")
        raw_modifiers = arguments.get("modifiers", [])
        if not isinstance(raw_modifiers, list) or not all(
            isinstance(modifier, str) for modifier in raw_modifiers
        ):
            raise ValueError("'modifiers' must be a list of strings")
        element_ref = _optional_element_ref(arguments.get("element_ref"))
        result = await self._runtime.key(
            key,
            modifiers=tuple(raw_modifiers),
            element_ref=element_ref,
        )
        return result.model_dump(mode="json")


class ComputerScrollTool(BaseTool):
    """滚动（低风险，自动允许）。"""

    def __init__(self, runtime: ComputerRuntime) -> None:
        self._runtime = runtime

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="computer_scroll",
            description="滚动。delta_x / delta_y 至少一个非 0。",
            parameters={
                "type": "object",
                "properties": {
                    "delta_x": {
                        "type": "integer",
                        "description": "水平滚动量，默认 0。",
                    },
                    "delta_y": {
                        "type": "integer",
                        "description": "垂直滚动量，默认 0。",
                    },
                },
                "additionalProperties": False,
            },
            permission=ToolPermission.ALLOWED,
            ui_scope=ToolUiScope.DESKTOP,
            strict=False,
        )

    async def execute(self, arguments: dict[str, Any]) -> Any:
        return await self._execute(arguments)

    async def execute_with_context(
        self,
        arguments: dict[str, Any],
        context: Any,
    ) -> Any:
        return await self._execute(arguments)

    async def _execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        delta_x = arguments.get("delta_x", 0)
        delta_y = arguments.get("delta_y", 0)
        if not isinstance(delta_x, int) or isinstance(delta_x, bool):
            raise ValueError("'delta_x' must be an integer")
        if not isinstance(delta_y, int) or isinstance(delta_y, bool):
            raise ValueError("'delta_y' must be an integer")
        if delta_x == 0 and delta_y == 0:
            raise ValueError("at least one of delta_x/delta_y must be non-zero")
        result = await self._runtime.scroll(delta_x=delta_x, delta_y=delta_y)
        return result.model_dump(mode="json")


class ComputerOpenAppTool(BaseTool):
    """打开应用（低风险，自动允许）。"""

    def __init__(self, runtime: ComputerRuntime) -> None:
        self._runtime = runtime

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="computer_open_app",
            description="打开一个应用。",
            parameters={
                "type": "object",
                "properties": {
                    "app": {
                        "type": "string",
                        "description": "应用名称或 bundle id。",
                    },
                },
                "required": ["app"],
                "additionalProperties": False,
            },
            permission=ToolPermission.ALLOWED,
            ui_scope=ToolUiScope.DESKTOP,
            strict=False,
        )

    async def execute(self, arguments: dict[str, Any]) -> Any:
        return await self._execute(arguments)

    async def execute_with_context(
        self,
        arguments: dict[str, Any],
        context: Any,
    ) -> Any:
        return await self._execute(arguments)

    async def _execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        app = arguments.get("app")
        if not isinstance(app, str) or not app.strip():
            raise ValueError("'app' must be a non-empty string")
        result = await self._runtime.open_app(app)
        return result.model_dump(mode="json")


class ComputerFocusWindowTool(BaseTool):
    """聚焦窗口（低风险，自动允许）。"""

    def __init__(self, runtime: ComputerRuntime) -> None:
        self._runtime = runtime

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="computer_focus_window",
            description="聚焦某个窗口（window_ref 来自 Observation.windows）。",
            parameters={
                "type": "object",
                "properties": {
                    "window_ref": {
                        "type": "string",
                        "description": "窗口引用。",
                    },
                },
                "required": ["window_ref"],
                "additionalProperties": False,
            },
            permission=ToolPermission.ALLOWED,
            ui_scope=ToolUiScope.DESKTOP,
            strict=False,
        )

    async def execute(self, arguments: dict[str, Any]) -> Any:
        return await self._execute(arguments)

    async def execute_with_context(
        self,
        arguments: dict[str, Any],
        context: Any,
    ) -> Any:
        return await self._execute(arguments)

    async def _execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        window_ref = arguments.get("window_ref")
        if not isinstance(window_ref, str) or not window_ref.strip():
            raise ValueError("'window_ref' must be a non-empty string")
        result = await self._runtime.focus_window(window_ref)
        return result.model_dump(mode="json")


def _bounded_observation_payload(
    payload: dict[str, Any],
    *,
    max_chars: int = 18_000,
) -> dict[str, Any]:
    """在工具执行器截断前裁剪元素，保证 Observation 始终是完整 JSON。"""

    raw_elements = payload.get("elements")
    if not isinstance(raw_elements, list):
        return payload
    bounded = {key: value for key, value in payload.items() if key != "elements"}
    bounded["elements"] = []
    bounded["element_stats"] = {
        "observed": len(raw_elements),
        "returned": 0,
    }
    for element in raw_elements:
        bounded["elements"].append(element)
        bounded["element_stats"]["returned"] = len(bounded["elements"])
        encoded = json.dumps(
            bounded,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if len(encoded) > max_chars:
            bounded["elements"].pop()
            bounded["element_stats"]["returned"] = len(bounded["elements"])
            bounded["truncated"] = True
            break
    return bounded


def _optional_element_ref(raw: Any) -> str | None:
    """校验可选的 element_ref；None / 未提供 → None，非法 → ValueError。"""

    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("'element_ref' must be a non-empty string")
    return raw.strip()


def _parse_click_target(
    arguments: dict[str, Any],
) -> ElementTarget | CoordinateTarget:
    """把 computer_click 的 JSON 参数解析成 ElementTarget / CoordinateTarget。"""

    observation_id = arguments.get("observation_id")
    if not isinstance(observation_id, str) or not observation_id.strip():
        raise ValueError("'observation_id' must be a non-empty string")

    raw_element = arguments.get("element_ref")
    has_element = isinstance(raw_element, str) and bool(raw_element.strip())
    has_x = isinstance(arguments.get("x"), int) and not isinstance(
        arguments.get("x"),
        bool,
    )
    has_y = isinstance(arguments.get("y"), int) and not isinstance(
        arguments.get("y"),
        bool,
    )

    if has_element and (has_x or has_y):
        raise ValueError("provide either element_ref or x/y, not both")
    if has_element:
        return ElementTarget(
            observation_id=observation_id,
            element_ref=raw_element.strip(),
        )
    if has_x and has_y:
        return CoordinateTarget(
            observation_id=observation_id,
            x=arguments["x"],
            y=arguments["y"],
        )
    raise ValueError(
        "computer_click requires either element_ref or both x and y"
    )


def register_computer_tools(
    registry: ToolRegistry,
    computer_runtime: ComputerRuntime,
) -> None:
    """把 7 个 computer_* 工具注册进现有 ToolRegistry。"""

    registry.register(ComputerObserveTool(computer_runtime))
    registry.register(ComputerClickTool(computer_runtime))
    registry.register(ComputerTypeTool(computer_runtime))
    registry.register(ComputerKeyTool(computer_runtime))
    registry.register(ComputerScrollTool(computer_runtime))
    registry.register(ComputerOpenAppTool(computer_runtime))
    registry.register(ComputerFocusWindowTool(computer_runtime))


__all__ = [
    "ComputerClickTool",
    "ComputerFocusWindowTool",
    "ComputerKeyTool",
    "ComputerObserveTool",
    "ComputerOpenAppTool",
    "ComputerScrollTool",
    "ComputerTypeTool",
    "register_computer_tools",
]
