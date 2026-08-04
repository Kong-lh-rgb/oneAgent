"""搜索提供商的基础接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .types import SearchRequest, SearchResponse


class SearchProvider(ABC):
    """把统一搜索请求转换为具体搜索服务调用。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """返回稳定的提供商名称。"""

    @abstractmethod
    async def search(self, request: SearchRequest) -> SearchResponse:
        """执行一次异步搜索。"""


__all__ = ["SearchProvider"]
