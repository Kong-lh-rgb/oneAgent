"""网络搜索工具（需人工审核）。

默认使用 Bing 搜索端点（无需 API Key），解析返回的链接与摘要；
DuckDuckGo 作为备选引擎。网络搜索属于外部访问，权限档位为 HUMAN_APPROVAL。
"""

from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, quote_plus, urlsplit

import httpx

from app.models.types import ToolDefinition, ToolPermission

from ..base import BaseTool

MAX_RESULTS_CAP = 10
_SEARCH_ENGINES: dict[str, str] = {
    "bing": "https://www.bing.com/search?q={query}",
    "duckduckgo": "https://lite.duckduckgo.com/lite/?q={query}",
}

Fetcher = Callable[[str], Awaitable[str]]


class WebSearchTool(BaseTool):
    def __init__(
        self,
        *,
        fetcher: Fetcher | None = None,
        search_engine: str = "bing",
        search_url_template: str | None = None,
        max_results_cap: int = MAX_RESULTS_CAP,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        engine = search_engine.strip().lower()
        if engine not in _SEARCH_ENGINES:
            raise ValueError(
                f"'search_engine' must be one of {sorted(_SEARCH_ENGINES)}"
            )
        self._engine = engine
        self._fetcher = fetcher
        self._search_url_template = search_url_template or _SEARCH_ENGINES[engine]
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

        if self._engine == "bing":
            results = _parse_bing_results(html, max_results)
        else:
            results = _parse_lite_results(html, max_results)
        return {
            "query": query,
            "count": len(results),
            "results": results,
        }

    async def _fetch_html(self, query: str) -> str:
        """抓取搜索结果页。

        注意：不覆盖 User-Agent。实测 Bing 对浏览器 UA 会返回不含
        ``b_algo`` 结构的页面，而默认 httpx UA 会返回可解析的标准 SERP。
        """
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


class _BingResultsParser(HTMLParser):
    """解析 Bing 搜索结果页（li.b_algo 结果块）。

    标题取 ``h2 > a`` 里的文本，摘要取标题之后的 ``p`` 文本；
    链接是 ``bing.com/ck/a`` 重定向时解码 ``u=`` 参数还原真实 URL。
    """

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._in_block = False
        self._got_title = False
        self._in_h2 = False
        self._in_title_link = False
        self._in_snippet = False
        self._url: str | None = None
        self._title: list[str] = []
        self._snippet_parts: list[str] = []
        self._snippet: list[str] = []

    def _begin_block(self) -> None:
        self._in_block = True
        self._got_title = False
        self._in_h2 = False
        self._in_title_link = False
        self._in_snippet = False
        self._url = None
        self._title = []
        self._snippet_parts = []
        self._snippet = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key: value for key, value in attrs}
        if tag == "li" and "b_algo" in (attr_map.get("class") or ""):
            self._begin_block()
            return
        if not self._in_block:
            return
        if tag == "h2":
            self._in_h2 = True
            return
        if tag == "a" and self._in_h2 and not self._got_title:
            href = attr_map.get("href") or ""
            if href.startswith("http"):
                self._got_title = True
                self._in_title_link = True
                self._url = href
            return
        if tag == "p" and self._got_title:
            self._in_snippet = True
            self._snippet = []

    def handle_data(self, data: str) -> None:
        if self._in_title_link:
            self._title.append(data)
        elif self._in_snippet:
            self._snippet.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_title_link:
            self._in_title_link = False
        elif tag == "h2" and self._in_h2:
            self._in_h2 = False
        elif tag == "p" and self._in_snippet:
            text = " ".join("".join(self._snippet).split())
            if text:
                self._snippet_parts.append(text)
            self._in_snippet = False
        elif tag == "li" and self._in_block:
            title = "".join(self._title).strip()
            if title and self._url:
                self.results.append(
                    {
                        "title": title,
                        "url": _clean_bing_url(self._url),
                        "snippet": " ".join(self._snippet_parts),
                    }
                )
            self._in_block = False


def _clean_bing_url(href: str) -> str:
    """把 Bing 的 /ck/a 重定向链接还原为真实目标 URL。"""
    if "bing.com/ck/a" not in href:
        return href
    encoded = parse_qs(urlsplit(href).query).get("u", [""])[0]
    if not encoded.startswith("a1"):
        return href
    body = encoded[2:]
    try:
        decoded = base64.urlsafe_b64decode(
            body + "=" * (-len(body) % 4)
        ).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return href
    return decoded if decoded.startswith(("http://", "https://")) else href


def _parse_bing_results(html: str, max_results: int) -> list[dict[str, str]]:
    parser = _BingResultsParser()
    parser.feed(html)
    return parser.results[:max_results]
