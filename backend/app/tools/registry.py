"""本地工具注册表。"""

from __future__ import annotations

import re
from collections.abc import Collection

from app.models.types import AgentMode, ToolDefinition

from .base import BaseTool

_VALID_NAME = re.compile(r"^[a-zA-Z0-9_]+$")

# Plan 模式允许的工具白名单（只读 / 搜索 / 规划）。
# 不允许任何会修改用户环境或产生外部副作用的工具：
# 文件写/删（write_file）、shell、网络写（http_request）、automation 创建/控制、
# memory/skill 修改、tool_search（激活延迟工具）等一律排除。
# 执行层（AgentRuntime._execute_tool）同时按此白名单硬阻断，不仅隐藏定义。
_PLAN_MODE_ALLOWED_TOOLS = frozenset(
    {
        "read_file",
        "list_files",
        "web_search",
        "current_time",
        "memory_read",
        "task_create",
        "task_update",
        "task_get",
        "task_list",
    }
)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}
        self._deferred_names: set[str] = set()

    def register(self, tool: BaseTool, *, deferred: bool = False) -> None:
        """注册工具；延迟工具只在当前 Run 被激活后暴露完整定义。"""

        name = tool.definition.name
        if not name:
            raise ValueError("Tool name cannot be empty.")
        if not _VALID_NAME.fullmatch(name):
            raise ValueError(
                "Tool name must use dot-separated letters, digits, or underscores: "
                f"{name!r}"
            )
        if name in self._tools:
            raise ValueError(f"Tool '{name}' is already registered.")
        self._tools[name] = tool
        if deferred:
            self._deferred_names.add(name)

    def unregister(self, name: str) -> BaseTool:
        """注销工具并返回被移除的工具；不存在时抛 KeyError。"""
        try:
            tool = self._tools.pop(name)
        except KeyError:
            raise KeyError(f"Tool '{name}' is not registered.") from None
        self._deferred_names.discard(name)
        return tool

    def get(self, name: str) -> BaseTool:
        try:
            return self._tools[name]
        except KeyError:
            raise KeyError(f"Tool '{name}' is not registered.") from None

    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def deferred_names(self) -> tuple[str, ...]:
        """返回按注册顺序排列的延迟加载工具名。"""

        return tuple(name for name in self._tools if name in self._deferred_names)

    def is_deferred(self, name: str) -> bool:
        return name in self._deferred_names

    def model_definitions(
        self,
        *,
        activated_names: Collection[str] = (),
    ) -> tuple[ToolDefinition, ...]:
        """返回当前 Run 可见定义：常驻工具加已激活的延迟工具。"""

        activated = set(activated_names)
        return tuple(
            tool.definition
            for name, tool in self._tools.items()
            if tool.definition.permission.model_visible()
            and (name not in self._deferred_names or name in activated)
        )

    def allowed_names_for_mode(self, mode: AgentMode) -> frozenset[str]:
        """返回该模式下允许执行的工具名集合。

        - NORMAL：注册表中的全部工具；
        - PLAN：只读 / 搜索 / 规划工具白名单。
        """

        if mode is AgentMode.PLAN:
            return _PLAN_MODE_ALLOWED_TOOLS
        return frozenset(self._tools)

    def is_allowed_for_mode(self, name: str, mode: AgentMode) -> bool:
        """工具是否允许在该模式执行（执行层的硬性能力过滤）。"""

        return name in self.allowed_names_for_mode(mode)

    def model_definitions_for_mode(
        self,
        mode: AgentMode,
        *,
        activated_names: Collection[str] = (),
    ) -> tuple[ToolDefinition, ...]:
        """返回该模式可见的工具定义（PLAN 模式隐藏副作用工具）。"""

        allowed = self.allowed_names_for_mode(mode)
        activated = set(activated_names)
        return tuple(
            tool.definition
            for name, tool in self._tools.items()
            if name in allowed
            and tool.definition.permission.model_visible()
            and (name not in self._deferred_names or name in activated)
        )

    def definitions(
        self,
        *,
        for_model: bool = True,
    ) -> tuple[ToolDefinition, ...]:
        """返回工具定义。

        ``for_model=True`` 时排除 FORBIDDEN 档位的工具（严格禁止模型执行）。
        """
        if not for_model:
            return tuple(tool.definition for tool in self._tools.values())
        return tuple(
            tool.definition
            for tool in self._tools.values()
            if tool.definition.permission.model_visible()
        )
