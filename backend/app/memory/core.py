"""Core Memory（``CORE.md``）的加载与受控更新。

Core Memory 是模型每次运行都应该知道的信息，注入 System Prompt。
只保存用户身份、稳定长期偏好、长期约束等极少数真正长期有效的全局规则。

Core Memory 不参与普通 Memory 淘汰，也不允许模型因推断随意修改。
只有检测到用户明确长期信息时，才通过 ``update`` 更新。
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, field_validator

from app.context.tokens import default_token_estimator

logger = logging.getLogger("oneagent.memory.core")

DEFAULT_MAX_CORE_TOKENS = 2_000
_CORE_FORMAT = "oneagent-core-v1"
_CORE_HEADING = "# Core Memory"
_MANAGED_HEADING = "## Managed Core Entries"
_CORE_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
_MAX_CORE_VALUE_CHARS = 1_000
_MAX_CORE_REASON_CHARS = 1_000
_MAX_SOURCE_STATEMENT_CHARS = 2_000


class CoreMemoryEntry(BaseModel):
    """模型提出、Harness 按稳定 key 管理的一条 Core Memory。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    value: str
    reason: str
    source_statement: str
    updated_at: datetime

    @field_validator("key", mode="before")
    @classmethod
    def normalize_key(cls, value: object) -> str:
        return normalize_core_key(value)

    @field_validator("value", "reason", "source_statement", mode="before")
    @classmethod
    def normalize_text(cls, value: object) -> str:
        if not isinstance(value, str):
            raise TypeError("core memory value and reason must be strings")
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("core memory value and reason cannot be empty")
        return normalized

    @field_validator("value")
    @classmethod
    def validate_value_length(cls, value: str) -> str:
        if len(value) > _MAX_CORE_VALUE_CHARS:
            raise ValueError(
                f"core memory value exceeds {_MAX_CORE_VALUE_CHARS} characters"
            )
        return value

    @field_validator("reason")
    @classmethod
    def validate_reason_length(cls, value: str) -> str:
        if len(value) > _MAX_CORE_REASON_CHARS:
            raise ValueError(
                f"core memory reason exceeds {_MAX_CORE_REASON_CHARS} characters"
            )
        return value

    @field_validator("source_statement")
    @classmethod
    def validate_source_statement_length(cls, value: str) -> str:
        if len(value) > _MAX_SOURCE_STATEMENT_CHARS:
            raise ValueError(
                "core memory source statement exceeds "
                f"{_MAX_SOURCE_STATEMENT_CHARS} characters"
            )
        return value

    @field_validator("updated_at")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("core memory timestamp must include timezone")
        return value.astimezone(UTC)


class CoreMemoryManager:
    """CORE.md 的加载与受控更新。"""

    def __init__(
        self,
        memory_dir: str | Path,
        *,
        max_tokens: int = DEFAULT_MAX_CORE_TOKENS,
    ) -> None:
        self.path = Path(memory_dir) / "CORE.md"
        self.max_tokens = max_tokens
        if max_tokens <= 0:
            raise ValueError("max_tokens must be greater than zero")

    async def initialize(self) -> None:
        await asyncio.to_thread(self.path.parent.mkdir, parents=True, exist_ok=True)

    async def load(self) -> str:
        """加载模型可见正文；Front Matter 运行元数据不进入上下文。"""

        if not await asyncio.to_thread(self.path.is_file):
            return ""
        if await asyncio.to_thread(self.path.is_symlink):
            raise ValueError("CORE.md cannot be a symbolic link")
        content = await asyncio.to_thread(self.path.read_text, encoding="utf-8")
        _, visible = _parse_document(content)
        estimated = self._estimate_tokens(visible)
        if estimated > self.max_tokens:
            raise ValueError(
                f"core memory exceeds token limit: {estimated} > {self.max_tokens}"
            )
        return visible

    async def update(self, content: str) -> None:
        """受控更新 CORE.md。由显式长期信息触发，不用于模型普通写入。"""

        normalized = content.strip()
        if not normalized:
            raise ValueError("core memory content cannot be empty")
        estimated = self._estimate_tokens(normalized)
        if estimated > self.max_tokens:
            raise ValueError(
                f"core memory exceeds token limit: {estimated} > {self.max_tokens}"
            )
        await asyncio.to_thread(self._write_atomic, normalized + "\n")

    async def upsert(
        self,
        *,
        key: str,
        value: str,
        reason: str,
        source_statement: str,
    ) -> tuple[CoreMemoryEntry, bool]:
        """按 key 创建或更新 Core 条目，并保留其他条目与人工正文。"""

        now = datetime.now(UTC)
        entry = CoreMemoryEntry(
            key=key,
            value=value,
            reason=reason,
            source_statement=source_statement,
            updated_at=now,
        )
        raw = ""
        if await asyncio.to_thread(self.path.is_file):
            if await asyncio.to_thread(self.path.is_symlink):
                raise ValueError("CORE.md cannot be a symbolic link")
            raw = await asyncio.to_thread(self.path.read_text, encoding="utf-8")
        entries, visible = _parse_document(raw)
        created = entry.key not in entries
        entries[entry.key] = entry
        legacy = _legacy_content(visible)
        rendered, visible_rendered = _render_document(entries, legacy=legacy)
        estimated = self._estimate_tokens(visible_rendered)
        if estimated > self.max_tokens:
            raise ValueError(
                f"core memory exceeds token limit: {estimated} > {self.max_tokens}"
            )
        await asyncio.to_thread(self._write_atomic, rendered)
        return entry, created

    async def remove(self, key: str) -> CoreMemoryEntry:
        """移除一个结构化 Core 条目，不允许模型重写其他内容。"""

        normalized_key = normalize_core_key(key)
        if not await asyncio.to_thread(self.path.is_file):
            raise KeyError(f"core memory key not found: {normalized_key}")
        if await asyncio.to_thread(self.path.is_symlink):
            raise ValueError("CORE.md cannot be a symbolic link")
        raw = await asyncio.to_thread(self.path.read_text, encoding="utf-8")
        entries, visible = _parse_document(raw)
        removed = entries.pop(normalized_key, None)
        if removed is None:
            raise KeyError(f"core memory key not found: {normalized_key}")
        rendered, visible_rendered = _render_document(
            entries,
            legacy=_legacy_content(visible),
        )
        estimated = self._estimate_tokens(visible_rendered)
        if estimated > self.max_tokens:
            raise ValueError(
                f"core memory exceeds token limit: {estimated} > {self.max_tokens}"
            )
        await asyncio.to_thread(self._write_atomic, rendered)
        return removed

    def _estimate_tokens(self, content: str) -> int:
        try:
            estimator = default_token_estimator()
            return estimator.estimate_text(content)
        except Exception:
            return len(content) // 2

    def _write_atomic(self, content: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp-{os.getpid()}")
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, self.path)


def _parse_document(text: str) -> tuple[dict[str, CoreMemoryEntry], str]:
    """解析结构化 Core；旧的纯 Markdown 文件作为可保留正文处理。"""

    stripped = text.strip()
    if not stripped.startswith("---"):
        return {}, stripped
    lines = stripped.splitlines()
    closing = next(
        (index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"),
        None,
    )
    if closing is None:
        return {}, stripped
    metadata = yaml.safe_load("\n".join(lines[1:closing]))
    if not isinstance(metadata, dict) or metadata.get("format") != _CORE_FORMAT:
        return {}, stripped
    raw_entries = metadata.get("entries", [])
    if not isinstance(raw_entries, list):
        raise ValueError("CORE.md entries metadata must be a list")
    entries = {
        entry.key: entry
        for entry in (
            CoreMemoryEntry.model_validate(raw_entry) for raw_entry in raw_entries
        )
    }
    visible = "\n".join(lines[closing + 1 :]).strip()
    return entries, visible


def normalize_core_key(value: object) -> str:
    """规范化受 Harness 管理的 Core 条目 key。"""

    if not isinstance(value, str):
        raise TypeError("core memory key must be a string")
    normalized = value.strip().lower()
    if not _CORE_KEY_RE.fullmatch(normalized):
        raise ValueError("core memory key must be a lowercase dotted identifier")
    return normalized


def _legacy_content(visible: str) -> str:
    """保留结构化条目前的人工 Core 正文，避免首次 upsert 覆盖用户文件。"""

    normalized = visible.strip()
    if normalized.startswith(_CORE_HEADING):
        normalized = normalized[len(_CORE_HEADING) :].lstrip()
    if _MANAGED_HEADING in normalized:
        normalized = normalized.split(_MANAGED_HEADING, 1)[0].rstrip()
    return normalized


def _render_document(
    entries: dict[str, CoreMemoryEntry],
    *,
    legacy: str,
) -> tuple[str, str]:
    metadata = {
        "format": _CORE_FORMAT,
        "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "entries": [
            {
                "key": entry.key,
                "value": entry.value,
                "reason": entry.reason,
                "source_statement": entry.source_statement,
                "updated_at": entry.updated_at.isoformat(timespec="seconds"),
            }
            for entry in sorted(entries.values(), key=lambda item: item.key)
        ],
    }
    front = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False)
    body: list[str] = [_CORE_HEADING, ""]
    if legacy:
        body.extend((legacy, ""))
    body.extend((_MANAGED_HEADING, ""))
    for entry in sorted(entries.values(), key=lambda item: item.key):
        body.extend((f"### {entry.key}", "", entry.value, ""))
    visible = "\n".join(body).rstrip() + "\n"
    return f"---\n{front}---\n{visible}", visible


__all__ = [
    "CoreMemoryEntry",
    "CoreMemoryManager",
    "DEFAULT_MAX_CORE_TOKENS",
    "normalize_core_key",
]
