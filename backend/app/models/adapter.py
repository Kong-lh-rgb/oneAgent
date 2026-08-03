"""模型适配器的抽象接口。"""

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
        """返回一次统一格式的模型响应。"""

    @abstractmethod
    async def close(self) -> None:
        """释放模型提供商客户端资源。"""
