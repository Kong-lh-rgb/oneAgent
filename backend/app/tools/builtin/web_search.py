"""网络搜索工具（需人工审核）。

默认使用 DuckDuckGo lite 端点（无需 API Key），解析返回的链接与摘要。
网络搜索属于外部访问，权限档位为 HUMAN_APPROVAL。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote_plus

import httpx

from app.models.types import ToolDefinition, ToolPermission

from ..base import BaseTool

DEFAULT_SEARCH_URL_TEMPLATE = "https://lite.duckduckgo.com/lite/?q={query}"
MAX_RESULTS_CAP = 10

Fetcher = Callable[[str], Awaitable[str]]


class WebSearchTool(BaseTool):
    def __init__(
        self,
        *,
        fetcher: Fetcher | None = None,
        search_url_template: str | None = None,
        max_results_cap: int = MAX_RESULTS_CAP,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._fetcher = fetcher
        self._search_url_template = search_url_template or DEFAULT_SEARCH_URL_TEMPLATE
        self._max_results_cap = max_results_cap
        self._client = client

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="web_search",
            description=(
                "Search the web for a query and return the top results with "
                "titles, URLs, and snippets. Requires human approval."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": (
                            f"Maximum number of results (capped at "
                            f"{self._max_results_cap})."
                        ),
                        "default": 5,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            strict=True,
            permission=ToolPermission.HUMAN_APPROVAL,
        )

    async def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("'query' must be a non-empty string")

        max_results = arguments.get("max_results", 5)
        if not isinstance(max_results, int) or max_results <= 0:
            raise ValueError("'max_results' must be a positive integer")
        max_results = min(max_results, self._max_results_cap)

        if self._fetcher is not None:
            html = await self._fetcher(query)
        else:
            html = await self._fetch_html(query)

        results = _parse_lite_results(html, max_results)
        return {
            "query": query,
            "count": len(results),
            "results": results,
        }

    async def _fetch_html(self, query: str) -> str:
        url = self._search_url_template.format(query=quote_plus(query))
        if self._client is not None:
            response = await self._client.get(url, timeout=15.0)
            return response.text
        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
            response = await client.get(url)
            return response.text


class _LiteResultsParser(HTMLParser):
    """解析 DuckDuckGo lite 结果页的链接与摘要。"""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict[str, str]] = []
        self.snippets: list[str] = []
        self._in_link = False
        self._link_href: str | None = None
        self._link_text: list[str] = []
        self._in_snippet = False
        self._snippet: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key: value for key, value in attrs}
        href = attr_map.get("href") or ""
        if (
            tag == "a"
            and attr_map.get("rel") == "nofollow"
            and href.startswith("http")
        ):
            self._in_link = True
            self._link_href = href
            self._link_text = []
        elif tag == "td" and "result-snippet" in (attr_map.get("class") or ""):
            self._in_snippet = True
            self._snippet = []

    def handle_data(self, data: str) -> None:
        if self._in_link:
            self._link_text.append(data)
        elif self._in_snippet:
            self._snippet.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_link:
            title = "".join(self._link_text).strip()
            if title and self._link_href:
                self.links.append({"title": title, "url": self._link_href})
            self._in_link = False
            self._link_href = None
            self._link_text = []
        elif tag == "td" and self._in_snippet:
            self.snippets.append(" ".join("".join(self._snippet).split()))
            self._in_snippet = False


def _parse_lite_results(html: str, max_results: int) -> list[dict[str, str]]:
    parser = _LiteResultsParser()
    parser.feed(html)
    results: list[dict[str, str]] = []
    for index, link in enumerate(parser.links[:max_results]):
        snippet = parser.snippets[index] if index < len(parser.snippets) else ""
        results.append({**link, "snippet": snippet})
    return results
