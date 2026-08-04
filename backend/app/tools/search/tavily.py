"""Tavily Search REST API 提供商。"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx

from .base import SearchProvider
from .errors import (
    SearchAuthenticationError,
    SearchError,
    SearchNetworkError,
    SearchNoResultsError,
    SearchRateLimitError,
)
from .types import SearchRequest, SearchResponse, SearchResult

TAVILY_SEARCH_URL = "https://api.tavily.com/search"


class TavilySearchProvider(SearchProvider):
    """使用 Tavily 返回适合 LLM 消费的结构化搜索结果。"""

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        normalized_key = api_key.strip()
        if not normalized_key:
            raise ValueError("Tavily API key cannot be empty")
        self._api_key = normalized_key
        self._timeout_seconds = timeout_seconds
        self._client = client

    @property
    def name(self) -> str:
        return "tavily"

    async def search(self, request: SearchRequest) -> SearchResponse:
        payload: dict[str, Any] = {
            "query": request.query,
            "search_depth": "basic",
            "topic": request.topic.value,
            "max_results": request.max_results,
            "include_answer": False,
            "include_raw_content": False,
            "auto_parameters": False,
        }
        if request.time_range is not None:
            payload["time_range"] = request.time_range.value
        if request.include_domains:
            payload["include_domains"] = list(request.include_domains)
        if request.exclude_domains:
            payload["exclude_domains"] = list(request.exclude_domains)

        response = await self._post(payload)
        self._raise_for_status(response)
        try:
            body = response.json()
        except ValueError as exc:
            raise SearchNetworkError("Tavily returned invalid JSON") from exc
        if not isinstance(body, dict):
            raise SearchNetworkError("Tavily returned an invalid response body")

        raw_results = body.get("results")
        if not isinstance(raw_results, list):
            raise SearchNetworkError("Tavily response is missing results")

        results: list[SearchResult] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if urlsplit(url).scheme not in {"http", "https"}:
                continue
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            raw_score = item.get("score")
            score = float(raw_score) if isinstance(raw_score, (int, float)) else None
            results.append(
                SearchResult(
                    title=title,
                    url=url,
                    snippet=str(item.get("content") or "").strip(),
                    score=score,
                    published_at=(
                        str(item["published_date"])
                        if item.get("published_date")
                        else None
                    ),
                )
            )

        if not results:
            raise SearchNoResultsError("Tavily returned no usable results")
        return SearchResponse(
            query=request.query,
            provider=self.name,
            results=tuple(results[: request.max_results]),
        )

    async def _post(self, payload: dict[str, Any]) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            if self._client is not None:
                return await self._client.post(
                    TAVILY_SEARCH_URL,
                    json=payload,
                    headers=headers,
                    timeout=self._timeout_seconds,
                )
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                return await client.post(
                    TAVILY_SEARCH_URL,
                    json=payload,
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            raise SearchNetworkError(f"Tavily request failed: {exc}") from exc

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code in {401, 403}:
            raise SearchAuthenticationError(
                "Tavily API key is invalid or lacks permission"
            )
        if response.status_code in {429, 432, 433}:
            raise SearchRateLimitError(
                f"Tavily quota or rate limit reached ({response.status_code})"
            )
        if response.status_code >= 500:
            raise SearchNetworkError(
                f"Tavily service error ({response.status_code})"
            )
        if response.status_code >= 400:
            raise SearchError(f"Tavily request rejected ({response.status_code})")


__all__ = ["TAVILY_SEARCH_URL", "TavilySearchProvider"]
