"""从工具注册表动态生成的可搜索工具目录。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from app.models.types import ToolDefinition

from .base import BaseTool
from .registry import ToolRegistry

TOOL_SEARCH_NAME = "tool_search"
_ASCII_TOKEN_RE = re.compile(r"[a-z0-9_]+")
_CJK_RE = re.compile(r"[\u3400-\u9fff]+")


@dataclass(frozen=True, slots=True)
class ToolCatalogMatch:
    """一次目录检索命中的精简工具信息。"""

    name: str
    description: str
    parameter_names: tuple[str, ...]
    score: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": list(self.parameter_names),
        }


class ToolCatalog:
    """每次搜索直接读取 Registry，因此工具增删无需维护额外索引。"""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def search(self, query: str, *, limit: int = 5) -> tuple[ToolCatalogMatch, ...]:
        normalized_query = " ".join(query.casefold().split())
        if not normalized_query:
            raise ValueError("query 不能为空")
        if not 1 <= limit <= 5:
            raise ValueError("limit 必须在 1 到 5 之间")

        matches: list[ToolCatalogMatch] = []
        for name in self._registry.deferred_names():
            definition = self._registry.get(name).definition
            if not definition.permission.model_visible():
                continue
            score = _relevance_score(normalized_query, definition)
            if score <= 0:
                continue
            properties = definition.parameters.get("properties", {})
            parameter_names = (
                tuple(str(key) for key in properties)
                if isinstance(properties, dict)
                else ()
            )
            matches.append(
                ToolCatalogMatch(
                    name=name,
                    description=_compact_text(definition.description, max_chars=500),
                    parameter_names=parameter_names,
                    score=score,
                )
            )
        matches.sort(key=lambda item: (-item.score, item.name))
        return tuple(matches[:limit])


class ToolSearchTool(BaseTool):
    """让模型搜索并激活当前 Registry 中的延迟工具。"""

    definition = ToolDefinition(
        name=TOOL_SEARCH_NAME,
        description=(
            "搜索当前可用但尚未加载的工具。需要外部或 MCP 能力时先调用；"
            "第三方工具通常使用英文描述，中文需求请同时提供对应英文关键词。"
            "命中的工具会在下一步自动加载完整定义。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "能力关键词，建议包含英文同义词，如 weather forecast"
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "最多返回的工具数，默认 5，范围 1 到 5",
                    "minimum": 1,
                    "maximum": 5,
                    "default": 5,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    )

    def __init__(self, registry: ToolRegistry) -> None:
        self._catalog = ToolCatalog(registry)

    async def execute(self, arguments: dict[str, Any]) -> str:
        query = arguments.get("query")
        if not isinstance(query, str):
            raise TypeError("query 必须是字符串")
        limit = arguments.get("limit", 5)
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError("limit 必须是整数")
        matches = self._catalog.search(query, limit=limit)
        payload = {
            "query": query,
            "count": len(matches),
            "tools": [match.as_dict() for match in matches],
            "hint": (
                "这些工具已激活，可在下一步直接调用。"
                if matches
                else "没有匹配工具，请换用更接近工具名称或英文描述的关键词。"
            ),
        }
        return json.dumps(payload, ensure_ascii=False)


def ensure_tool_search_registered(registry: ToolRegistry) -> None:
    """存在延迟工具时，确保目录搜索工具只注册一次。"""

    if not registry.deferred_names():
        return
    try:
        existing = registry.get(TOOL_SEARCH_NAME)
    except KeyError:
        registry.register(ToolSearchTool(registry))
        return
    if not isinstance(existing, ToolSearchTool):
        raise ValueError(f"Tool name '{TOOL_SEARCH_NAME}' is reserved.")


def activated_tool_names(output: str | None) -> tuple[str, ...]:
    """从 tool_search 的受控输出中读取待激活工具名。"""

    if not output:
        return ()
    try:
        payload = json.loads(output)
        tools = payload.get("tools", [])
        return tuple(
            item["name"]
            for item in tools
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        )
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        return ()


def _relevance_score(query: str, definition: ToolDefinition) -> int:
    properties = definition.parameters.get("properties", {})
    parameter_text = ""
    if isinstance(properties, dict):
        parameter_text = " ".join(
            f"{name} {value.get('description', '') if isinstance(value, dict) else ''}"
            for name, value in properties.items()
        )
    name = definition.name.casefold()
    description = definition.description.casefold()
    searchable = f"{name} {description} {parameter_text.casefold()}"
    score = 0
    if query in searchable:
        score += 30
    for token in _query_tokens(query):
        if token in name:
            score += 12
        elif token in description:
            score += 6
        elif token in searchable:
            score += 3
    return score


def _query_tokens(query: str) -> tuple[str, ...]:
    tokens = list(_ASCII_TOKEN_RE.findall(query))
    for sequence in _CJK_RE.findall(query):
        if len(sequence) <= 2:
            tokens.append(sequence)
        else:
            tokens.extend(
                sequence[index : index + 2]
                for index in range(len(sequence) - 1)
            )
    return tuple(dict.fromkeys(token for token in tokens if len(token) >= 2))


def _compact_text(value: str, *, max_chars: int) -> str:
    compacted = " ".join(value.split())
    if len(compacted) <= max_chars:
        return compacted
    return f"{compacted[:max_chars]}…"


__all__ = [
    "TOOL_SEARCH_NAME",
    "ToolCatalog",
    "ToolCatalogMatch",
    "ToolSearchTool",
    "activated_tool_names",
    "ensure_tool_search_registered",
]
