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
from pathlib import Path

from app.context.tokens import default_token_estimator

logger = logging.getLogger("oneagent.memory.core")

DEFAULT_MAX_CORE_TOKENS = 2_000


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
        """加载 CORE.md；不存在时返回空字符串。"""

        if not await asyncio.to_thread(self.path.is_file):
            return ""
        if await asyncio.to_thread(self.path.is_symlink):
            raise ValueError("CORE.md cannot be a symbolic link")
        content = await asyncio.to_thread(self.path.read_text, encoding="utf-8")
        estimated = self._estimate_tokens(content)
        if estimated > self.max_tokens:
            raise ValueError(
                f"core memory exceeds token limit: {estimated} > {self.max_tokens}"
            )
        return content

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


__all__ = ["CoreMemoryManager", "DEFAULT_MAX_CORE_TOKENS"]
