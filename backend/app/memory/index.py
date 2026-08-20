"""长期记忆 Index（``INDEX.md``）的生成与加载。

INDEX.md 是 Memory Store 的 projection，不是模型手工维护的文件。每次
``memory_create`` / ``memory_update`` / ``memory_archive`` 后由 Runtime
调用 ``rebuild()`` 重新生成。

INDEX 只保存 Recall Cue（id + title + summary），不保存完整正文。
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Sequence
from pathlib import Path

from .models import MemoryRecord

logger = logging.getLogger("vesta.memory.index")

_INDEX_HEADER = (
    "# Long-term Memory Index\n\n"
    "The following long-term memories are available.\n"
    "Use memory_read only when one of these memories may materially "
    "help the current task.\n"
)


class MemoryIndex:
    """INDEX.md 的读写。"""

    def __init__(self, memory_dir: str | Path) -> None:
        self.path = Path(memory_dir) / "INDEX.md"

    def render(self, memories: Sequence[MemoryRecord]) -> str:
        """根据 active 记忆渲染 INDEX 内容。"""

        if not memories:
            return _INDEX_HEADER + "\n(No long-term memories yet.)\n"
        lines = [_INDEX_HEADER]
        for record in memories:
            cue = _single_line(record.summary) or record.title
            lines.append(f"[{record.id}] {record.title}")
            lines.append(f"Cue: {cue}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    async def rebuild(self, memories: Sequence[MemoryRecord]) -> None:
        """原子写入 INDEX.md。"""

        content = self.render(memories)
        await asyncio.to_thread(self._write_atomic, content)

    async def load(self) -> str | None:
        """读取 INDEX.md；不存在时返回 None。"""

        if not await asyncio.to_thread(self.path.is_file):
            return None
        return await asyncio.to_thread(self.path.read_text, encoding="utf-8")

    def _write_atomic(self, content: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp-{os.getpid()}")
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, self.path)


def _single_line(text: str) -> str:
    return " ".join(text.split()).strip()


__all__ = ["MemoryIndex"]
