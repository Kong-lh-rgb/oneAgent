"""Abstract model adapter contract."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .config import ProviderConfig
from .types import ModelRequest, ModelResponse


class ModelAdapter(ABC):
    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    @property
    def provider(self) -> str:
        return self.config.provider

    @property
    def default_model(self) -> str:
        return self.config.model

    @abstractmethod
    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Return one normalized model response."""

    @abstractmethod
    async def close(self) -> None:
        """Release provider client resources."""
