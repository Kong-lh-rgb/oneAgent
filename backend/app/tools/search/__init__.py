"""与模型无关的统一网页搜索服务。"""

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
from .service import SearchService, build_search_service
from .tavily import TavilySearchProvider
from .types import (
    SearchRequest,
    SearchResponse,
    SearchResult,
    SearchTimeRange,
    SearchTopic,
)

__all__ = [
    "DuckDuckGoSearchProvider",
    "SearchAuthenticationError",
    "SearchError",
    "SearchNetworkError",
    "SearchNoResultsError",
    "SearchProvider",
    "SearchProviderName",
    "SearchRateLimitError",
    "SearchRequest",
    "SearchResponse",
    "SearchResult",
    "SearchService",
    "SearchSettings",
    "SearchTimeRange",
    "SearchTopic",
    "SearchUnavailableError",
    "TavilySearchProvider",
    "build_search_service",
]
