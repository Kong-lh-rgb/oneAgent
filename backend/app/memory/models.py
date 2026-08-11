"""长期记忆（Long-term Memory）数据模型。

每个普通长期记忆保存为一个带 YAML Front Matter 的 Markdown 文件：

```text
active/M001.md
```

Front Matter 保存 id、标题、摘要与运行时元数据；正文按固定小节组织，
供模型通过 ``memory.read`` 完整读取。
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

_MEMORY_ID_PREFIX = "M"
_MEMORY_ID_RE = re.compile(r"^M[0-9]{3,}$")
_MAX_TITLE_CHARS = 200
_MAX_SUMMARY_CHARS = 500
_MAX_CONTENT_CHARS = 12_000
_MAX_REASON_CHARS = 1_000


class MemoryStatus(StrEnum):
    """普通长期记忆的生命周期状态。"""

    ACTIVE = "active"
    ARCHIVED = "archived"


class MemoryRecord(BaseModel):
    """一条普通长期记忆及其运行时元数据。

    运行时字段（``created_at``/``updated_at``/``last_accessed_at``/
    ``access_count``/``status``）由 Memory Store 维护，模型不能自行填写。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^M[0-9]{3,}$")
    title: str
    summary: str
    content: str
    created_at: datetime
    updated_at: datetime
    last_accessed_at: datetime
    access_count: int = Field(default=0, ge=0)
    status: MemoryStatus = MemoryStatus.ACTIVE
    last_update_reason: str | None = None
    archive_reason: str | None = None

    @field_validator("title", "summary", mode="before")
    @classmethod
    def normalize_cue_text(cls, value: object) -> str:
        """标题和 Recall Cue 必须紧凑，避免 INDEX 被长文本撑大。"""

        if not isinstance(value, str):
            raise TypeError("memory title and summary must be strings")
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("memory title and summary cannot be empty")
        return normalized

    @field_validator("title")
    @classmethod
    def validate_title_length(cls, value: str) -> str:
        if len(value) > _MAX_TITLE_CHARS:
            raise ValueError(f"memory title exceeds {_MAX_TITLE_CHARS} characters")
        return value

    @field_validator("summary")
    @classmethod
    def validate_summary_length(cls, value: str) -> str:
        if len(value) > _MAX_SUMMARY_CHARS:
            raise ValueError(
                f"memory summary exceeds {_MAX_SUMMARY_CHARS} characters"
            )
        return value

    @field_validator("content", mode="before")
    @classmethod
    def normalize_content(cls, value: object) -> str:
        if not isinstance(value, str):
            raise TypeError("memory content must be a string")
        normalized = value.strip()
        if not normalized:
            raise ValueError("memory content cannot be empty")
        if len(normalized) > _MAX_CONTENT_CHARS:
            raise ValueError(
                f"memory content exceeds {_MAX_CONTENT_CHARS} characters"
            )
        return normalized

    @field_validator("last_update_reason", "archive_reason", mode="before")
    @classmethod
    def normalize_reason(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("memory change reason must be a string")
        normalized = " ".join(value.split())
        if not normalized:
            return None
        if len(normalized) > _MAX_REASON_CHARS:
            raise ValueError(
                f"memory change reason exceeds {_MAX_REASON_CHARS} characters"
            )
        return normalized

    def front_matter(self) -> dict[str, object]:
        """序列化为 Front Matter 字典（运行时元数据）。"""

        metadata: dict[str, object] = {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
            "last_accessed_at": _iso(self.last_accessed_at),
            "access_count": self.access_count,
            "status": self.status.value,
        }
        if self.last_update_reason is not None:
            metadata["last_update_reason"] = self.last_update_reason
        if self.archive_reason is not None:
            metadata["archive_reason"] = self.archive_reason
        return metadata

    def render_markdown(self) -> str:
        """渲染为带 Front Matter 的 Markdown 文件内容。"""

        front = yaml.safe_dump(
            self.front_matter(),
            allow_unicode=True,
            sort_keys=False,
        )
        body = "\n".join(
            (
                f"# {self.title}",
                "",
                "## Summary",
                "",
                self.summary,
                "",
                "## Memory",
                "",
                self.content,
                "",
            )
        )
        return f"---\n{front}---\n{body}"

    def render_full(self) -> str:
        """渲染为模型 ``memory.read`` 可见的完整正文（不含 Front Matter）。"""

        return (
            f"# {self.title}\n\n"
            f"## Summary\n\n{self.summary}\n\n"
            f"## Memory\n\n{self.content}"
        )


def parse_memory_markdown(text: str) -> MemoryRecord:
    """从文件内容解析 MemoryRecord；Front Matter 为权威元数据。"""

    front, body = _split_front_matter(text)
    if front is None:
        raise ValueError("memory file is missing YAML front matter")
    data = yaml.safe_load(front)
    if not isinstance(data, dict):
        raise ValueError("memory front matter must be a mapping")
    return MemoryRecord(
        id=str(data["id"]),
        title=str(data["title"]),
        summary=str(data.get("summary", "")),
        content=_extract_memory_section(body),
        created_at=_parse_iso(str(data["created_at"])),
        updated_at=_parse_iso(str(data["updated_at"])),
        last_accessed_at=_parse_iso(str(data["last_accessed_at"])),
        access_count=int(data.get("access_count", 0)),
        status=MemoryStatus(str(data.get("status", MemoryStatus.ACTIVE.value))),
        last_update_reason=data.get("last_update_reason"),
        archive_reason=data.get("archive_reason"),
    )


def normalize_memory_id(memory_id: str) -> str:
    """校验并规范化模型提供的 Memory ID，禁止把 ID 当作文件路径。"""

    if not isinstance(memory_id, str):
        raise TypeError("memory id must be a string")
    normalized = memory_id.strip().upper()
    if not _MEMORY_ID_RE.fullmatch(normalized):
        raise ValueError("memory id must match M followed by at least three digits")
    return normalized


def next_memory_id(existing_ids: set[str]) -> str:
    """分配下一个形如 M001 的 Memory ID（在现有 ID 之后递增）。"""

    highest = 0
    for memory_id in existing_ids:
        suffix = memory_id.removeprefix(_MEMORY_ID_PREFIX)
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return f"{_MEMORY_ID_PREFIX}{highest + 1:03d}"


def _split_front_matter(text: str) -> tuple[str | None, str]:
    """把 ``---`` 包裹的 Front Matter 与正文分开。"""

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join(lines[1:index]), "\n".join(lines[index + 1 :]).strip()
    return None, text


def _extract_memory_section(body: str) -> str:
    """从正文中提取 ``## Memory`` 之后的完整记忆正文。"""

    lines = body.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == "## Memory":
            return "\n".join(lines[index + 1 :]).strip()
    return body.strip()


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds")


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("memory datetimes must include timezone information")
    return parsed.astimezone(UTC)


__all__ = [
    "MemoryRecord",
    "MemoryStatus",
    "next_memory_id",
    "normalize_memory_id",
    "parse_memory_markdown",
]
