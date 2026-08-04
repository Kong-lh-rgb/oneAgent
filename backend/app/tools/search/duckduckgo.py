"""无需 API Key 的 DuckDuckGo Lite 降级搜索提供商。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote_plus, unquote, urlsplit

import httpx

from .base import SearchProvider
from .errors import SearchNetworkError, SearchNoResultsError
from .types import SearchRequest, SearchResponse, SearchResult

DUCKDUCKGO_SEARCH_URL = "https://lite.duckduckgo.com/lite/?q={query}"

Fetcher = Callable[[str], Awaitable[str]]


class DuckDuckGoSearchProvider(SearchProvider):
    """解析 DuckDuckGo Lite HTML，作为无密钥的尽力而为降级源。"""

    def __init__(
        self,
        *,
        timeout_seconds: float = 15.0,
        client: httpx.AsyncClient | None = None,
        fetcher: Fetcher | None = None,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._client = client
        self._fetcher = fetcher

    @property
    def name(self) -> str:
        return "duckduckgo"

    async def search(self, request: SearchRequest) -> SearchResponse:
        query = _provider_query(request)
        html = (
            await self._fetcher(query)
            if self._fetcher is not None
            else await self._fetch_html(query)
        )
        results = _parse_lite_results(html, request.max_results)
        if not results:
            raise SearchNoResultsError("DuckDuckGo returned no usable results")
        return SearchResponse(
            query=request.query,
            provider=self.name,
            results=tuple(results),
        )

    async def _fetch_html(self, query: str) -> str:
        url = DUCKDUCKGO_SEARCH_URL.format(query=quote_plus(query))
        try:
            if self._client is not None:
                response = await self._client.get(url, timeout=self._timeout_seconds)
            else:
                async with httpx.AsyncClient(
                    follow_redirects=True,
                    timeout=self._timeout_seconds,
                ) as client:
                    response = await client.get(url)
        except httpx.HTTPError as exc:
            raise SearchNetworkError(f"DuckDuckGo request failed: {exc}") from exc

        if response.status_code >= 400:
            raise SearchNetworkError(
                f"DuckDuckGo service error ({response.status_code})"
            )
        content_type = response.headers.get("content-type", "")
        if content_type and "html" not in content_type.lower():
            raise SearchNetworkError("DuckDuckGo returned a non-HTML response")
        return response.text


class _LiteResultsParser(HTMLParser):
    """解析 DuckDuckGo Lite 结果页的链接与摘要。"""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self.snippets: list[str] = []
        self._in_link = False
        self._link_href: str | None = None
        self._link_text: list[str] = []
        self._in_snippet = False
        self._snippet: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key: value for key, value in attrs}
        href = attr_map.get("href") or ""
        if tag == "a" and attr_map.get("rel") == "nofollow" and href:
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
            title = " ".join("".join(self._link_text).split())
            url = _clean_result_url(self._link_href or "")
            if title and url:
                self.links.append((title, url))
            self._in_link = False
            self._link_href = None
            self._link_text = []
        elif tag == "td" and self._in_snippet:
            self.snippets.append(" ".join("".join(self._snippet).split()))
            self._in_snippet = False


def _provider_query(request: SearchRequest) -> str:
    parts = [request.query]
    if request.include_domains:
        domains = " OR ".join(f"site:{domain}" for domain in request.include_domains)
        parts.append(f"({domains})")
    parts.extend(f"-site:{domain}" for domain in request.exclude_domains)
    return " ".join(parts)


def _clean_result_url(href: str) -> str:
    if href.startswith("//"):
        href = f"https:{href}"
    parsed = urlsplit(href)
    if "duckduckgo.com" in (parsed.hostname or ""):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            href = unquote(target)
            parsed = urlsplit(href)
    return href if parsed.scheme in {"http", "https"} else ""


def _parse_lite_results(html: str, max_results: int) -> list[SearchResult]:
    parser = _LiteResultsParser()
    parser.feed(html)
    results: list[SearchResult] = []
    for index, (title, url) in enumerate(parser.links[:max_results]):
        snippet = parser.snippets[index] if index < len(parser.snippets) else ""
        results.append(SearchResult(title=title, url=url, snippet=snippet))
    return results


__all__ = [
    "DUCKDUCKGO_SEARCH_URL",
    "DuckDuckGoSearchProvider",
]
