"""Skill 的 Markdown 文件存储。

每个 Skill 保存为 ``skills/<name>.md``。技能是预置的、由开发者或用户维护的
可复用流程，系统只负责加载与列出，不提供模型写入（自动生成 Skill 属于边界外）。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .models import Skill, parse_skill_markdown

DEFAULT_SKILLS_DIR = Path(__file__).resolve().parents[2] / ".oneagent" / "skills"
_MAX_SKILL_FILE_BYTES = 256_000

logger = logging.getLogger("oneagent.skills.store")


class SkillStore:
    """Skill 的加载与列出。"""

    def __init__(self, skills_dir: str | Path = DEFAULT_SKILLS_DIR) -> None:
        self.skills_dir = Path(skills_dir).expanduser().resolve()

    async def initialize(self) -> None:
        """创建 skills 目录。"""

        await asyncio.to_thread(self.skills_dir.mkdir, parents=True, exist_ok=True)

    async def list(self) -> tuple[Skill, ...]:
        """列出所有 Skill（name + description + 正文），按名称排序。"""

        skills: list[Skill] = []
        for path in sorted(self.skills_dir.glob("*.md")):
            if await asyncio.to_thread(path.is_symlink):
                continue
            try:
                skills.append(await asyncio.to_thread(_read_skill, path))
            except (ValueError, OSError) as exc:
                logger.warning("skip unreadable skill %s: %s", path.name, exc)
        return tuple(skills)

    async def load(self, name: str) -> Skill | None:
        """按名称加载 Skill；不存在或损坏时返回 None。"""

        normalized = name.strip().lower()
        path = self.skills_dir / f"{normalized}.md"
        if not await asyncio.to_thread(path.is_file):
            return None
        if await asyncio.to_thread(path.is_symlink):
            return None
        try:
            return await asyncio.to_thread(_read_skill, path)
        except (ValueError, OSError):
            return None


def _read_skill(path: Path) -> Skill:
    if path.stat().st_size > _MAX_SKILL_FILE_BYTES:
        raise ValueError(f"skill file too large: {path.name}")
    text = path.read_text(encoding="utf-8")
    skill = parse_skill_markdown(text)
    if path.stem != skill.name:
        raise ValueError(
            f"skill file name '{path.stem}' does not match front matter name "
            f"'{skill.name}'"
        )
    return skill


__all__ = ["DEFAULT_SKILLS_DIR", "SkillStore"]
