"""Skill Store：双层发现 + 激活加载 + 资源清单。

- ``catalog()`` 只建立轻量 metadata（不读完整正文）；
- ``load(name)`` 在激活时才读取 SKILL.md 正文与资源清单；
- 所有路径经 discovery 安全解析，越界/符号链接一律拒绝。
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import uuid4

import yaml

from .config import SkillSettings
from .discovery import (
    DEFAULT_PROJECT_SKILLS_DIR,
    DEFAULT_USER_SKILLS_DIR,
    SkillDiagnostic,
    SkillDiscovery,
    safe_skill_dir,
    safe_skill_file,
)
from .models import (
    Skill,
    SkillMetadata,
    SkillResources,
    SkillScope,
    validate_skill_name,
)
from .parser import SkillParseError, parse_skill_document

_DISABLED_DIR_NAME = ".disabled"


@dataclass(frozen=True)
class ManagedSkillEntry:
    """供人类管理界面展示的 Skill，不进入模型上下文。"""

    metadata: SkillMetadata
    enabled: bool


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

    async def managed_catalog(self) -> tuple[ManagedSkillEntry, ...]:
        """列出启用与停用 Skill；Project/User 同名项分别保留。"""

        # 先刷新正常 Discovery 诊断；管理视图不能使用 catalog() 的同名覆盖结果，
        # 否则 project 同名 Skill 会让 user Skill 在设置页中不可管理。
        self.discovery.discover()
        active = [
            ManagedSkillEntry(metadata=item, enabled=True)
            for scope, root in (
                (SkillScope.PROJECT, self.project_dir),
                (SkillScope.USER, self.user_dir),
            )
            for item in self._scope_catalog(root, scope)
        ]
        disabled = [
            ManagedSkillEntry(metadata=item, enabled=False)
            for scope, root in (
                (SkillScope.PROJECT, self.project_dir),
                (SkillScope.USER, self.user_dir),
            )
            for item in self._scope_catalog(root / _DISABLED_DIR_NAME, scope)
        ]
        return tuple(
            sorted(
                (*active, *disabled),
                key=lambda item: (
                    item.metadata.name,
                    item.metadata.scope.value,
                    not item.enabled,
                ),
            )
        )

    async def install(
        self,
        *,
        name: str,
        description: str,
        instructions: str,
        scope: SkillScope = SkillScope.PROJECT,
    ) -> Skill:
        """安装新 Skill；生成合法 SKILL.md 并以目录为单位原子落盘。"""

        normalized_name = validate_skill_name(name.strip())
        normalized_description = description.strip()
        normalized_instructions = instructions.strip()
        root = self.project_dir if scope is SkillScope.PROJECT else self.user_dir
        target = root / normalized_name
        disabled_target = root / _DISABLED_DIR_NAME / normalized_name
        if target.exists() or disabled_target.exists():
            raise ValueError(f"skill '{normalized_name}' already exists")

        front_matter = yaml.safe_dump(
            {
                "name": normalized_name,
                "description": normalized_description,
            },
            allow_unicode=True,
            sort_keys=False,
        ).strip()
        markdown = f"---\n{front_matter}\n---\n\n{normalized_instructions}\n"
        # 写入前复用正式 Parser 校验，避免 UI 与 Runtime 出现两套格式规则。
        parse_skill_document(markdown, expected_name=normalized_name)

        root.mkdir(parents=True, exist_ok=True)
        temporary = root / f".{normalized_name}.{uuid4().hex}.tmp"
        try:
            temporary.mkdir()
            skill_file = temporary / "SKILL.md"
            with skill_file.open("w", encoding="utf-8") as handle:
                handle.write(markdown)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

        installed = await self.load(normalized_name)
        if installed is None:
            raise RuntimeError("installed skill could not be loaded")
        return installed

    async def install_package(
        self,
        *,
        files: Mapping[str, bytes],
        scope: SkillScope = SkillScope.PROJECT,
    ) -> Skill:
        """安装已下载的静态 Skill 包；不执行包内脚本。"""

        skill_document = files.get("SKILL.md")
        if not isinstance(skill_document, bytes):
            raise ValueError("Skill package is missing SKILL.md")
        try:
            text = skill_document.decode("utf-8")
        except UnicodeError as exc:
            raise ValueError("SKILL.md must be UTF-8") from exc

        # 先从 Front Matter 取得 name，再使用正式 Parser 做完整校验。
        lines = text.splitlines()
        closing = next(
            (
                index
                for index, line in enumerate(lines[1:], start=1)
                if line.strip() == "---"
            ),
            None,
        )
        front = (
            yaml.safe_load("\n".join(lines[1:closing]))
            if lines and lines[0].strip() == "---" and closing is not None
            else None
        )
        if not isinstance(front, dict) or not isinstance(front.get("name"), str):
            raise ValueError("Skill package has invalid front matter")
        name = validate_skill_name(front["name"])
        parse_skill_document(text, expected_name=name)

        root = self._root(scope)
        target = root / name
        disabled_target = root / _DISABLED_DIR_NAME / name
        if target.exists() or disabled_target.exists():
            raise ValueError(f"skill '{name}' already exists")

        normalized_files: dict[PurePosixPath, bytes] = {}
        total_bytes = 0
        for relative, content in files.items():
            if not isinstance(relative, str) or not isinstance(content, bytes):
                raise ValueError("Skill package files must be byte mappings")
            path = PurePosixPath(relative)
            if path.is_absolute() or ".." in path.parts or "." in path.parts:
                raise ValueError("Skill package contains an unsafe path")
            if path.as_posix() != "SKILL.md" and (
                len(path.parts) < 2
                or path.parts[0] not in {"scripts", "references", "assets"}
            ):
                raise ValueError("Skill package contains an unsupported file")
            total_bytes += len(content)
            if total_bytes > 10 * 1024 * 1024:
                raise ValueError("Skill package exceeds 10MB")
            normalized_files[path] = content

        root.mkdir(parents=True, exist_ok=True)
        temporary = root / f".{name}.{uuid4().hex}.tmp"
        try:
            temporary.mkdir()
            for relative, content in normalized_files.items():
                destination = temporary.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("wb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

        installed = await self.load(name)
        if installed is None:
            if target.exists():
                shutil.rmtree(target)
            raise RuntimeError("installed skill package could not be loaded")
        return installed

    async def set_enabled(
        self,
        *,
        name: str,
        scope: SkillScope,
        enabled: bool,
    ) -> ManagedSkillEntry:
        """通过受控目录移动启用/停用 Skill；停用不会删除任何内容。"""

        normalized_name = validate_skill_name(name)
        root = self._root(scope)
        source_root = root / _DISABLED_DIR_NAME if enabled else root
        target_root = root if enabled else root / _DISABLED_DIR_NAME
        source = safe_skill_dir(source_root, normalized_name)
        if source is None:
            raise KeyError(f"skill '{normalized_name}' not found")
        target = target_root / normalized_name
        if target.exists():
            raise ValueError(f"skill '{normalized_name}' target already exists")
        target_root.mkdir(parents=True, exist_ok=True)
        os.replace(source, target)
        metadata = self._read_metadata_at(target, scope)
        if metadata is None:
            # 正式 Skill 在移动前已可发现；若移动后无法读取，立即回滚。
            os.replace(target, source)
            raise RuntimeError("moved skill could not be loaded")
        return ManagedSkillEntry(metadata=metadata, enabled=enabled)

    async def delete(
        self,
        *,
        name: str,
        scope: SkillScope,
        enabled: bool,
    ) -> None:
        """删除指定作用域和状态下的 Skill 目录。"""

        normalized_name = validate_skill_name(name)
        root = self._root(scope)
        source_root = root if enabled else root / _DISABLED_DIR_NAME
        source = safe_skill_dir(source_root, normalized_name)
        if source is None:
            raise KeyError(f"skill '{normalized_name}' not found")
        shutil.rmtree(source)

    def _root(self, scope: SkillScope) -> Path:
        return self.project_dir if scope is SkillScope.PROJECT else self.user_dir

    def _scope_catalog(
        self,
        root: Path,
        scope: SkillScope,
    ) -> tuple[SkillMetadata, ...]:
        empty = root.parent / f".management-empty-{scope.value}"
        discovery = SkillDiscovery(
            project_dir=root if scope is SkillScope.PROJECT else empty,
            user_dir=root if scope is SkillScope.USER else empty,
        )
        return discovery.discover()

    def _read_metadata_at(
        self,
        skill_dir: Path,
        scope: SkillScope,
    ) -> SkillMetadata | None:
        catalog = self._scope_catalog(skill_dir.parent, scope)
        return next(
            (
                item
                for item in catalog
                if item.name == skill_dir.name and item.scope is scope
            ),
            None,
        )

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


__all__ = ["ManagedSkillEntry", "SkillStore"]
