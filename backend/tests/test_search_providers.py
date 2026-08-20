"""统一搜索 Provider、降级策略和 WebSearchTool 测试。"""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import SecretStr

from app.models.types import ToolCall
from app.tools import ToolExecutor, ToolRegistry, WebSearchTool
from app.tools.search import (
    DuckDuckGoSearchProvider,
    SearchAuthenticationError,
    SearchNetworkError,
    SearchNoResultsError,
    SearchProvider,
    SearchProviderName,
    SearchRequest,
    SearchResponse,
    SearchResult,
    SearchService,
    SearchSettings,
    SearchUnavailableError,
    TavilySearchProvider,
    build_search_service,
)


class StubSearchProvider(SearchProvider):
    def __init__(
        self,
        name: str,
        outcome: SearchResponse | Exception,
    ) -> None:
        self._name = name
        self._outcome = outcome
        self.requests: list[SearchRequest] = []

    @property
    def name(self) -> str:
        return self._name

    async def search(self, request: SearchRequest) -> SearchResponse:
        self.requests.append(request)
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def response(
    provider: str,
    *,
    title: str = "Result",
    url: str = "https://example.com/article",
    snippet: str = "useful content",
) -> SearchResponse:
    return SearchResponse(
        query="Vesta",
        provider=provider,
        results=(
            SearchResult(
                title=title,
                url=url,
                snippet=snippet,
                score=0.9,
            ),
        ),
    )


@pytest.mark.asyncio
async def test_tavily_search_maps_request_and_response() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "河北天气预报",
                        "url": "https://weather.example/hebei",
                        "content": "未来三天气温",
                        "score": 0.87,
                        "published_date": "2026-08-04",
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = TavilySearchProvider("tvly-test", client=client)

    result = await provider.search(
        SearchRequest(
            query="河北天气",
            topic="news",
            time_range="week",
            max_results=3,
            include_domains=("weather.com.cn",),
        )
    )

    assert captured["authorization"] == "Bearer tvly-test"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["search_depth"] == "basic"
    assert payload["include_raw_content"] is False
    assert payload["auto_parameters"] is False
    assert payload["time_range"] == "week"
    assert payload["include_domains"] == ["weather.com.cn"]
    assert result.provider == "tavily"
    assert result.results[0].score == 0.87
    assert result.results[0].published_at == "2026-08-04"


@pytest.mark.asyncio
async def test_tavily_authentication_error_is_explicit() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(401, json={"detail": "invalid key"})
        )
    )

    with pytest.raises(SearchAuthenticationError, match="API key"):
        await TavilySearchProvider("bad-key", client=client).search(
            SearchRequest(query="test")
        )


@pytest.mark.asyncio
async def test_duckduckgo_parses_redirect_url_and_domain_filters() -> None:
    captured: list[str] = []
    html = """
    <a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fa.example%2Fnews">
      Alpha News
    </a>
    <td class="result-snippet">first snippet</td>
    """

    async def fetcher(query: str) -> str:
        captured.append(query)
        return html

    provider = DuckDuckGoSearchProvider(fetcher=fetcher)
    result = await provider.search(
        SearchRequest(
            query="AI news",
            include_domains=("a.example",),
            exclude_domains=("spam.example",),
        )
    )

    assert "site:a.example" in captured[0]
    assert "-site:spam.example" in captured[0]
    assert result.results[0].url == "https://a.example/news"
    assert result.results[0].snippet == "first snippet"


@pytest.mark.asyncio
async def test_duckduckgo_empty_page_is_not_reported_as_success() -> None:
    async def fetcher(query: str) -> str:
        return "<html><title>DuckDuckGo</title></html>"

    with pytest.raises(SearchNoResultsError, match="no usable results"):
        await DuckDuckGoSearchProvider(fetcher=fetcher).search(
            SearchRequest(query="missing")
        )


@pytest.mark.asyncio
async def test_search_service_falls_back_and_reports_reason() -> None:
    primary = StubSearchProvider(
        "tavily",
        SearchNetworkError("temporary failure"),
    )
    fallback = StubSearchProvider("duckduckgo", response("duckduckgo"))

    result = await SearchService(primary, fallback).search(
        SearchRequest(query="Vesta")
    )

    assert result.provider == "duckduckgo"
    assert result.fallback_used is True
    assert "temporary failure" in (result.fallback_reason or "")
    assert len(primary.requests) == 1
    assert len(fallback.requests) == 1


@pytest.mark.asyncio
async def test_search_authentication_error_does_not_hide_configuration_bug() -> None:
    primary = StubSearchProvider(
        "tavily",
        SearchAuthenticationError("invalid key"),
    )
    fallback = StubSearchProvider("duckduckgo", response("duckduckgo"))

    with pytest.raises(SearchAuthenticationError, match="invalid key"):
        await SearchService(primary, fallback).search(SearchRequest(query="test"))

    assert fallback.requests == []


@pytest.mark.asyncio
async def test_search_service_reports_when_both_providers_fail() -> None:
    primary = StubSearchProvider("tavily", SearchNetworkError("offline"))
    fallback = StubSearchProvider(
        "duckduckgo",
        SearchNoResultsError("blocked"),
    )

    with pytest.raises(SearchUnavailableError, match="tavily.*duckduckgo"):
        await SearchService(primary, fallback).search(SearchRequest(query="test"))


def test_auto_search_settings_select_provider_by_api_key() -> None:
    no_key = SearchSettings(_env_file=None, search_provider="auto")
    with_key = SearchSettings(
        _env_file=None,
        search_provider="auto",
        tavily_api_key=SecretStr("tvly-test"),
    )

    assert build_search_service(no_key).primary_provider == "duckduckgo"
    assert build_search_service(with_key).primary_provider == "tavily"


def test_explicit_tavily_requires_api_key() -> None:
    settings = SearchSettings(
        _env_file=None,
        search_provider=SearchProviderName.TAVILY,
    )

    with pytest.raises(SearchAuthenticationError, match="TAVILY_API_KEY"):
        build_search_service(settings)


@pytest.mark.asyncio
async def test_web_search_tool_returns_unified_result_without_approval() -> None:
    provider = StubSearchProvider(
        "tavily",
        response("tavily", snippet="x" * 800),
    )
    settings = SearchSettings(
        _env_file=None,
        search_max_results=3,
    )
    registry = ToolRegistry()
    registry.register(
        WebSearchTool(
            service=SearchService(provider),
            settings=settings,
        )
    )

    result = await ToolExecutor(registry).execute(
        ToolCall(
            id="search-1",
            name="web_search",
            arguments={
                "query": "Vesta",
                "max_results": 10,
                "topic": "general",
            },
        )
    )

    assert result.success is True
    output = json.loads(result.output or "{}")
    assert output["provider"] == "tavily"
    assert output["count"] == 1
    assert len(output["results"][0]["snippet"]) == 500
    assert provider.requests[0].max_results == 3
