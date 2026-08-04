"""搜索提供商的统一错误。"""


class SearchError(RuntimeError):
    """搜索错误的基类。"""


class SearchAuthenticationError(SearchError):
    """搜索 API Key 缺失、无效或无权限。"""


class SearchRateLimitError(SearchError):
    """搜索服务达到额度或请求频率上限。"""


class SearchNetworkError(SearchError):
    """搜索服务发生网络或服务端错误。"""


class SearchNoResultsError(SearchError):
    """搜索请求成功，但没有可用结果。"""


class SearchUnavailableError(SearchError):
    """主搜索与降级搜索均不可用。"""


__all__ = [
    "SearchAuthenticationError",
    "SearchError",
    "SearchNetworkError",
    "SearchNoResultsError",
    "SearchRateLimitError",
    "SearchUnavailableError",
]
