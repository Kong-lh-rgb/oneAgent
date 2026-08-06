"""OneAgent 本地工具的基础接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from app.models.types import ToolDefinition

if TYPE_CHECKING:
    from .hooks import ToolExecutionContext


class BaseTool(ABC):
    """在本地执行的异步工具。"""

    @property
    @abstractmethod
    def definition(self) -> ToolDefinition:
        """返回提供给模型的工具定义。"""

    @abstractmethod
    async def execute(self, arguments: dict[str, Any]) -> Any:
        """执行工具且不阻塞事件循环。"""

    async def execute_with_context(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> Any:
        """执行需要运行上下文的工具；普通工具默认复用 execute。"""

        return await self.execute(arguments)
