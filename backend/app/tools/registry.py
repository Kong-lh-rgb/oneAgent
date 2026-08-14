"""本地工具注册表。"""

from __future__ import annotations

import re
from collections.abc import Collection

from app.models.types import ToolDefinition

from .base import BaseTool

_VALID_NAME = re.compile(r"^[a-zA-Z0-9_]+$")


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
