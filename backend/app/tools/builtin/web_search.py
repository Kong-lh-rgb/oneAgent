"""与模型无关的只读网页搜索工具。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.models.types import ToolDefinition, ToolPermission

from ..base import BaseTool
from ..search import (
    SearchRequest,
    SearchService,
    SearchSettings,
    build_search_service,
)

MAX_QUERY_CHARS = 500


class WebSearchTool(BaseTool):
    """通过统一 SearchService 执行 Tavily 或 DuckDuckGo 搜索。"""

    def __init__(
        self,
        *,
        service: SearchService | None = None,
        settings: SearchSettings | None = None,
    ) -> None:
        resolved_settings = settings or SearchSettings()
        self._service = service or build_search_service(resolved_settings)
        self._max_results = resolved_settings.search_max_results

    @property
    def definition(self) -> ToolDefinition:
        current_date = datetime.now().astimezone().date().isoformat()
        return ToolDefinition(
            name="web_search",
            description=(
                "Search the web and return source titles, URLs, concise snippets, "
                "relevance scores, and publication dates when available. This is "
                "a read-only tool and does not require approval. Cite the returned "
                "sources in the final answer. Use a few focused searches instead "
                "of repeating broad query variations. The current date is "
                f"{current_date}."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The focused search query.",
                        "maxLength": MAX_QUERY_CHARS,
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results.",
                        "default": self._max_results,
                        "minimum": 1,
                        "maximum": self._max_results,
                    },
                    "topic": {
                        "type": "string",
                        "enum": ["general", "news", "finance"],
                        "default": "general",
                    },
                    "time_range": {
                        "type": "string",
                        "enum": ["day", "week", "month", "year"],
                        "description": "Optional recency filter.",
                    },
                    "include_domains": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 10,
                    },
                    "exclude_domains": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 10,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            # 可选参数不满足 OpenAI 严格模式“全部字段必须 required”的约束，
            # 参数正确性由 SearchRequest 在本地统一验证。
            strict=False,
            permission=ToolPermission.ALLOWED,
        )

    @property
    def provider_name(self) -> str:
        """返回当前首选搜索提供商名称，供 CLI 显示。"""

        return self._service.primary_provider

    async def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        raw_max_results = arguments.get("max_results", self._max_results)
        if isinstance(raw_max_results, bool) or not isinstance(raw_max_results, int):
            raise ValueError("'max_results' must be an integer")

        request = SearchRequest.model_validate(
            {
                **arguments,
                "max_results": min(raw_max_results, self._max_results),
            }
        )
        response = await self._service.search(request)
        output = response.model_dump(mode="json")
        output["count"] = len(response.results)
        return output


__all__ = ["MAX_QUERY_CHARS", "WebSearchTool"]
