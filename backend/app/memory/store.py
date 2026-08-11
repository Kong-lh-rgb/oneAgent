"""长期记忆的 Markdown 文件存储。

目录结构：

```text
.oneagent/memory/
├── CORE.md
├── INDEX.md
├── active/M001.md ...
└── archive/Mxxx.md
```

每个普通记忆是一个带 Front Matter 的 Markdown 文件。写入采用
"临时文件 + 原子替换"，避免进程中断产生损坏文件。
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from .models import (
    MemoryRecord,
    MemoryStatus,
    next_memory_id,
    normalize_memory_id,
    parse_memory_markdown,
)

DEFAULT_MEMORY_DIR = Path(__file__).resolve().parents[2] / ".oneagent" / "memory"
_MAX_MEMORY_FILE_BYTES = 512_000

logger = logging.getLogger("oneagent.memory.store")


class MemoryStore:
    """普通长期记忆的 Markdown 文件 CRUD。"""

    def __init__(
        self,
        memory_dir: str | Path = DEFAULT_MEMORY_DIR,
        *,
        max_active: int = 25,
    ) -> None:
        self.memory_dir = Path(memory_dir).expanduser().resolve()
        self.active_dir = self.memory_dir / "active"
        self.archive_dir = self.memory_dir / "archive"
        self.max_active = max_active
        if max_active <= 0:
            raise ValueError("max_active must be greater than zero")

    async def initialize(self) -> None:
        """创建目录，并修复归档中断留下的错位文件。"""

        await asyncio.to_thread(self.active_dir.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(self.archive_dir.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(self._repair_interrupted_archives)

    async def create(
        self,
        *,
        title: str,
        summary: str,
        content: str,
    ) -> MemoryRecord:
        """创建一个普通长期记忆。"""

        existing_ids = await self._all_ids()
        now = datetime.now(UTC)
        record = MemoryRecord(
            id=next_memory_id(existing_ids),
            title=title,
            summary=summary,
            content=content,
            created_at=now,
            updated_at=now,
            last_accessed_at=now,
        )
        await self._write(record)
        return record

    async def load(self, memory_id: str) -> MemoryRecord | None:
        """按 ID 加载记忆（不更新任何元数据）。"""

        normalized = normalize_memory_id(memory_id)
        path = await self._resolve_path(normalized)
        if path is None or not await asyncio.to_thread(path.is_file):
            return None
        return await asyncio.to_thread(_read_record, path)

    async def read(self, memory_id: str) -> MemoryRecord | None:
        """读取 active 记忆并自动维护 ``access_count`` / ``last_accessed_at``。

        归档记忆不进入模型上下文，因此不可通过语义 read 读取。
        """

        record = await self.load(memory_id)
        if record is None or record.status is MemoryStatus.ARCHIVED:
            return None
        updated = MemoryRecord(
            **{
                **record.model_dump(),
                "access_count": record.access_count + 1,
                "last_accessed_at": datetime.now(UTC),
            }
        )
        await self._write(updated)
        return updated

    async def update(
        self,
        memory_id: str,
        *,
        title: str | None = None,
        summary: str | None = None,
        content: str,
        reason: str,
        expected_revision: int | None = None,
    ) -> MemoryRecord:
        """更新记忆与 Recall Cue，并可拒绝基于旧 revision 的覆盖。"""

        record = await self.load(memory_id)
        if record is None:
            raise KeyError(f"memory '{memory_id}' not found")
        if record.status is not MemoryStatus.ACTIVE:
            raise ValueError("only active memory can be updated")
        if expected_revision is not None and record.revision != expected_revision:
            raise ValueError(
                f"memory '{record.id}' revision conflict: "
                f"expected {expected_revision}, current {record.revision}"
            )
        updated = MemoryRecord(
            **{
                **record.model_dump(),
                "title": title if title is not None else record.title,
                "summary": summary if summary is not None else record.summary,
                "content": content,
                "last_update_reason": reason,
                "updated_at": datetime.now(UTC),
                "revision": record.revision + 1,
            }
        )
        await self._write(updated)
        return updated

    async def archive(self, memory_id: str, *, reason: str) -> MemoryRecord:
        """把记忆从 ``active/`` 移到 ``archive/``。"""

        record = await self.load(memory_id)
        if record is None:
            raise KeyError(f"memory '{memory_id}' not found")
        if record.status is MemoryStatus.ARCHIVED:
            return record
        updated = MemoryRecord(
            **{
                **record.model_dump(),
                "status": MemoryStatus.ARCHIVED,
                "archive_reason": reason,
                "updated_at": datetime.now(UTC),
                "revision": record.revision + 1,
            }
        )
        source = self.active_dir / f"{record.id}.md"
        target = self.archive_dir / f"{record.id}.md"
        # 先原子更新源文件状态，再在同一文件系统中原子移动；正常路径不会留下
        # active/archive 双份记录。若第二步失败，尽力恢复原 active 文件。
        await asyncio.to_thread(self._write_bytes, updated.render_markdown(), source)
        try:
            await asyncio.to_thread(os.replace, source, target)
        except BaseException:
            await asyncio.to_thread(self._write_bytes, record.render_markdown(), source)
            raise
        return updated

    async def list_active(self) -> tuple[MemoryRecord, ...]:
        """列出所有 active 记忆，按 ID 升序。"""

        records: list[MemoryRecord] = []
        for path in sorted(self.active_dir.glob("M*.md")):
            if await asyncio.to_thread(path.is_symlink):
                continue
            try:
                record = await asyncio.to_thread(_read_record, path)
                if record.status is MemoryStatus.ACTIVE:
                    records.append(record)
            except (ValueError, OSError) as exc:
                logger.warning("skip unreadable memory %s: %s", path.name, exc)
        return tuple(sorted(records, key=lambda record: record.id))

    async def count_active(self) -> int:
        return len(await self.list_active())

    async def _all_ids(self) -> set[str]:
        ids: set[str] = set()
        for directory in (self.active_dir, self.archive_dir):
            for path in directory.glob("M*.md"):
                record = await self.load(path.stem)
                if record is not None:
                    ids.add(record.id)
        return ids

    async def _resolve_path(self, memory_id: str) -> Path | None:
        for directory in (self.active_dir, self.archive_dir):
            path = directory / f"{memory_id}.md"
            if await asyncio.to_thread(path.is_file) and not await asyncio.to_thread(
                path.is_symlink
            ):
                return path
        return None

    async def _write(self, record: MemoryRecord) -> None:
        if record.status is not MemoryStatus.ACTIVE:
            raise ValueError("inactive memory cannot be written to active directory")
        target = self.active_dir / f"{record.id}.md"
        await asyncio.to_thread(self._write_bytes, record.render_markdown(), target)

    def _write_bytes(self, content: str, target: Path) -> None:
        encoded = content.encode("utf-8")
        if len(encoded) > _MAX_MEMORY_FILE_BYTES:
            raise ValueError(
                f"memory file exceeds {_MAX_MEMORY_FILE_BYTES} bytes: {target.name}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.tmp-{os.getpid()}")
        temporary.write_bytes(encoded)
        os.replace(temporary, target)

    def _repair_interrupted_archives(self) -> None:
        """把已标记 archived 但仍位于 active/ 的文件移回 archive/。"""

        for path in self.active_dir.glob("M*.md"):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                if path.stat().st_size > _MAX_MEMORY_FILE_BYTES:
                    continue
                record = parse_memory_markdown(path.read_text(encoding="utf-8"))
                if record.id != path.stem or record.status is not MemoryStatus.ARCHIVED:
                    continue
                os.replace(path, self.archive_dir / path.name)
            except (OSError, ValueError) as exc:
                logger.warning(
                    "failed to repair interrupted memory archive %s: %s",
                    path.name,
                    exc,
                )


def _read_record(path: Path) -> MemoryRecord:
    if path.stat().st_size > _MAX_MEMORY_FILE_BYTES:
        raise ValueError(f"memory file too large: {path.name}")
    text = path.read_text(encoding="utf-8")
    record = parse_memory_markdown(text)
    if record.id != path.stem:
        raise ValueError(
            f"memory id does not match filename: {record.id} != {path.stem}"
        )
    expected_status = (
        MemoryStatus.ARCHIVED
        if path.parent.name == "archive"
        else MemoryStatus.ACTIVE
    )
    if record.status is not expected_status:
        raise ValueError(
            f"memory status does not match directory: {record.status.value}"
        )
    return record


__all__ = ["DEFAULT_MEMORY_DIR", "MemoryStore"]
