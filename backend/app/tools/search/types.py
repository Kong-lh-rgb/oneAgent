"""与具体搜索提供商无关的请求和结果模型。"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SearchTopic(StrEnum):
    GENERAL = "general"
    NEWS = "news"
    FINANCE = "finance"


class SearchTimeRange(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"


class SearchRequest(BaseModel):
    """一次统一的网页搜索请求。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = Field(min_length=1, max_length=500)
    max_results: int = Field(default=5, ge=1, le=10)
    topic: SearchTopic = SearchTopic.GENERAL
    time_range: SearchTimeRange | None = None
    include_domains: tuple[str, ...] = Field(default=(), max_length=10)
    exclude_domains: tuple[str, ...] = Field(default=(), max_length=10)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("query cannot be empty")
        return normalized

    @field_validator("include_domains", "exclude_domains")
    @classmethod
    def normalize_domains(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for value in values:
            domain = value.strip().lower().removeprefix("https://").removeprefix(
                "http://"
            )
            domain = domain.split("/", 1)[0]
            if domain and domain not in normalized:
                normalized.append(domain)
        return tuple(normalized)


class SearchResult(BaseModel):
    """单条搜索结果。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str
    url: str
    snippet: str = ""
    score: float | None = None
    published_at: str | None = None


class SearchResponse(BaseModel):
    """一个搜索提供商返回的统一结果。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str
    provider: str
    results: tuple[SearchResult, ...]
    fallback_used: bool = False
    fallback_reason: str | None = None


__all__ = [
    "SearchRequest",
    "SearchResponse",
    "SearchResult",
    "SearchTimeRange",
    "SearchTopic",
]
