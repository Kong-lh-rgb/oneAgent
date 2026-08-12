"""本地工具注册表。"""

from __future__ import annotations

import re

from app.models.types import ToolDefinition

from .base import BaseTool

_VALID_NAME = re.compile(r"^[a-zA-Z0-9_]+$")


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
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

    def unregister(self, name: str) -> BaseTool:
        """注销工具并返回被移除的工具；不存在时抛 KeyError。"""
        try:
            return self._tools.pop(name)
        except KeyError:
            raise KeyError(f"Tool '{name}' is not registered.") from None

    def get(self, name: str) -> BaseTool:
        try:
            return self._tools[name]
        except KeyError:
            raise KeyError(f"Tool '{name}' is not registered.") from None

    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

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
