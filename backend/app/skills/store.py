"""Skill Store：双层发现 + 激活加载 + 资源清单。

- ``catalog()`` 只建立轻量 metadata（不读完整正文）；
- ``load(name)`` 在激活时才读取 SKILL.md 正文与资源清单；
- 所有路径经 discovery 安全解析，越界/符号链接一律拒绝。
"""

from __future__ import annotations

from pathlib import Path

from .config import SkillSettings
from .discovery import (
    DEFAULT_PROJECT_SKILLS_DIR,
    DEFAULT_USER_SKILLS_DIR,
    SkillDiagnostic,
    SkillDiscovery,
    safe_skill_file,
)
from .models import Skill, SkillMetadata, SkillResources
from .parser import SkillParseError, parse_skill_document


class SkillStore:
    """Skill 的发现与激活加载。"""

    def __init__(
        self,
        user_dir: str | Path = DEFAULT_USER_SKILLS_DIR,
        project_dir: str | Path = DEFAULT_PROJECT_SKILLS_DIR,
        *,
        settings: SkillSettings | None = None,
    ) -> None:
        self.user_dir = Path(user_dir).expanduser().resolve()
        self.project_dir = Path(project_dir).expanduser().resolve()
        self.settings = settings or SkillSettings()
        self.discovery = SkillDiscovery(
            user_dir=self.user_dir,
            project_dir=self.project_dir,
        )

    async def initialize(self) -> None:
        """确保 project 根目录存在（可选目录不自动创建）。"""

        self.project_dir.mkdir(parents=True, exist_ok=True)

    async def catalog(self) -> tuple[SkillMetadata, ...]:
        """发现全部 Skill 的轻量 metadata（project 覆盖 user）。"""

        return self.discovery.discover()

    def diagnostics(self) -> tuple[SkillDiagnostic, ...]:
        return self.discovery.diagnostics()

    async def load(self, name: str) -> Skill | None:
        """按名称激活加载 Skill（正文 + 资源清单）；不存在返回 None。"""

        for metadata in await self.catalog():
            if metadata.name == name:
                return self._load_metadata(metadata)
        return None

    def _load_metadata(self, metadata: SkillMetadata) -> Skill | None:
        skill_dir = metadata.location.parent
        skill_file = safe_skill_file(skill_dir)
        if skill_file is None:
            return None
        try:
            text = skill_file.read_text(encoding="utf-8")
            parsed = parse_skill_document(text, expected_name=metadata.name)
        except (OSError, UnicodeError, SkillParseError):
            return None
        return Skill(
            metadata=metadata,
            content=parsed.body,
            root=skill_dir,
            resources=self._discover_resources(skill_dir),
        )

    def _discover_resources(self, skill_dir: Path) -> SkillResources:
        return SkillResources(
            scripts=_list_resource_dir(skill_dir, "scripts"),
            references=_list_resource_dir(skill_dir, "references"),
            assets=_list_resource_dir(skill_dir, "assets"),
        )


def _list_resource_dir(skill_dir: Path, subdir: str) -> tuple[str, ...]:
    directory = skill_dir / subdir
    if not directory.is_dir() or directory.is_symlink():
        return ()
    entries: list[str] = []
    for path in sorted(directory.rglob("*")):
        if path.is_file() and not path.is_symlink():
            entries.append(path.relative_to(skill_dir).as_posix())
    return tuple(entries)


__all__ = ["SkillStore"]
