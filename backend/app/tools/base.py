"""Base contract for local OneAgent tools."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.models.types import ToolDefinition


class BaseTool(ABC):
    """A locally executed asynchronous tool."""

    @property
    @abstractmethod
    def definition(self) -> ToolDefinition:
        """Return the schema exposed to models."""

    @abstractmethod
    async def execute(self, arguments: dict[str, Any]) -> Any:
        """Execute the tool without blocking the event loop."""
