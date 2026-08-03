"""OneAgent 本地工具的基础接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.models.types import ToolDefinition


class BaseTool(ABC):
    """在本地执行的异步工具。"""

    @property
    @abstractmethod
    def definition(self) -> ToolDefinition:
        """返回提供给模型的工具定义。"""

    @abstractmethod
    async def execute(self, arguments: dict[str, Any]) -> Any:
        """执行工具且不阻塞事件循环。"""
