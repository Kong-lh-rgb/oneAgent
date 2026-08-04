"""搜索提供商选择、降级和统一结果清理。"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from .base import SearchProvider
from .config import SearchProviderName, SearchSettings
from .duckduckgo import DuckDuckGoSearchProvider
from .errors import (
    SearchAuthenticationError,
    SearchError,
    SearchNetworkError,
    SearchNoResultsError,
    SearchRateLimitError,
    SearchUnavailableError,
)
from .tavily import TavilySearchProvider
from .types import SearchRequest, SearchResponse, SearchResult

MAX_TITLE_CHARS = 300
MAX_SNIPPET_CHARS = 500

_FALLBACK_ERRORS = (
    SearchNetworkError,
    SearchNoResultsError,
    SearchRateLimitError,
)


class SearchService:
    """调用主搜索提供商，并在可恢复错误时使用降级提供商。"""

    def __init__(
        self,
        primary: SearchProvider,
        fallback: SearchProvider | None = None,
    ) -> None:
        self._primary = primary
        self._fallback = fallback

    @property
    def primary_provider(self) -> str:
        return self._primary.name

    async def search(self, request: SearchRequest) -> SearchResponse:
        try:
            return _normalize_response(await self._primary.search(request), request)
        except SearchAuthenticationError:
            raise
        except _FALLBACK_ERRORS as primary_error:
            if self._fallback is None:
                raise SearchUnavailableError(
                    f"Search provider {self._primary.name!r} unavailable: "
                    f"{primary_error}"
                ) from primary_error
            try:
                response = await self._fallback.search(request)
                normalized = _normalize_response(response, request)
            except SearchError as fallback_error:
                raise SearchUnavailableError(
                    f"Search providers {self._primary.name!r} and "
                    f"{self._fallback.name!r} unavailable: "
                    f"{primary_error}; {fallback_error}"
                ) from fallback_error
            return normalized.model_copy(
                update={
                    "fallback_used": True,
                    "fallback_reason": (
                        f"{type(primary_error).__name__}: {primary_error}"
                    ),
                }
            )


def build_search_service(settings: SearchSettings | None = None) -> SearchService:
    """按照配置创建 Tavily 主搜索和 DuckDuckGo 降级链。"""

    resolved = settings or SearchSettings()
    duckduckgo = DuckDuckGoSearchProvider(
        timeout_seconds=resolved.search_timeout_seconds
    )
    api_key = resolved.tavily_api_key_value()

    if resolved.search_provider is SearchProviderName.DUCKDUCKGO:
        return SearchService(duckduckgo)
    if resolved.search_provider is SearchProviderName.TAVILY and api_key is None:
        raise SearchAuthenticationError(
            "SEARCH_PROVIDER=tavily requires TAVILY_API_KEY"
        )
    if api_key is None:
        return SearchService(duckduckgo)

    tavily = TavilySearchProvider(
        api_key,
        timeout_seconds=resolved.search_timeout_seconds,
    )
    return SearchService(tavily, duckduckgo)


def _normalize_response(
    response: SearchResponse,
    request: SearchRequest,
) -> SearchResponse:
    results: list[SearchResult] = []
    seen_urls: set[str] = set()
    for result in response.results:
        url = _normalize_url(result.url)
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        title = " ".join(result.title.split())[:MAX_TITLE_CHARS]
        if not title:
            continue
        results.append(
            result.model_copy(
                update={
                    "title": title,
                    "url": url,
                    "snippet": " ".join(result.snippet.split())[
                        :MAX_SNIPPET_CHARS
                    ],
                }
            )
        )
        if len(results) >= request.max_results:
            break

    if not results:
        raise SearchNoResultsError(
            f"Search provider {response.provider!r} returned no usable results"
        )
    return response.model_copy(
        update={
            "query": request.query,
            "results": tuple(results),
        }
    )


def _normalize_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


__all__ = ["SearchService", "build_search_service"]
