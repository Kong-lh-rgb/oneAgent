"""Skill Discovery：安全发现 User / Project 两层 Skill。

发现阶段只读取轻量 ``SkillMetadata``（name + description + 来源），不加载
正文。任何 Skill name 必须先经过严格校验，再参与路径计算，并用 ``resolve()``
确认最终路径仍位于允许的 Skill 根目录内。坏 Skill 被跳过并记录诊断，不影响
其余 Skill 与 Agent 启动。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .models import (
    SKILL_FILE_NAME,
    SkillMetadata,
    SkillScope,
    validate_skill_name,
)
from .parser import SkillParseError, parse_skill_document

logger = logging.getLogger("oneagent.skills.discovery")

MAX_SKILL_FILE_BYTES = 512_000

DEFAULT_USER_SKILLS_DIR = Path.home() / ".oneagent" / "skills"
DEFAULT_PROJECT_SKILLS_DIR = (
    Path(__file__).resolve().parents[2] / ".oneagent" / "skills"
)


@dataclass(frozen=True)
class SkillDiagnostic:
    """一条被跳过的坏 Skill 的诊断信息。"""

    scope: SkillScope
    name: str
    location: str
    reason: str

    def render(self) -> str:
        return (
            f"skill[{self.scope.value}] {self.name} at {self.location} "
            f"skipped: {self.reason}"
        )


class SkillDiscovery:
    """双层 Skill 目录的轻量发现。"""

    def __init__(
        self,
        user_dir: str | Path = DEFAULT_USER_SKILLS_DIR,
        project_dir: str | Path = DEFAULT_PROJECT_SKILLS_DIR,
    ) -> None:
        self.user_dir = Path(user_dir).expanduser().resolve()
        self.project_dir = Path(project_dir).expanduser().resolve()
        self._diagnostics: list[SkillDiagnostic] = []

    def diagnostics(self) -> tuple[SkillDiagnostic, ...]:
        return tuple(self._diagnostics)

    def discover(self) -> tuple[SkillMetadata, ...]:
        """发现全部 Skill（project 同名覆盖 user），按 name 稳定排序。"""

        self._diagnostics = []
        merged: dict[str, SkillMetadata] = {}
        for metadata in self._discover_scope(
            self.project_dir, SkillScope.PROJECT
        ):
            merged[metadata.name] = metadata
        for metadata in self._discover_scope(self.user_dir, SkillScope.USER):
            merged.setdefault(metadata.name, metadata)
        return tuple(sorted(merged.values(), key=lambda item: item.name))

    def _discover_scope(
        self,
        root: Path,
        scope: SkillScope,
    ) -> tuple[SkillMetadata, ...]:
        if not root.is_dir():
            return ()
        found: list[SkillMetadata] = []
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            name = child.name
            try:
                validate_skill_name(name)
            except ValueError as exc:
                self._record(scope, name, str(child), str(exc))
                continue
            skill_dir = safe_skill_dir(root, name)
            if skill_dir is None:
                self._record(
                    scope,
                    name,
                    str(child),
                    "skill directory escapes root or is a symlink",
                )
                continue
            metadata = self._read_metadata(skill_dir, scope)
            if metadata is not None:
                found.append(metadata)
        return tuple(found)

    def _read_metadata(
        self,
        skill_dir: Path,
        scope: SkillScope,
    ) -> SkillMetadata | None:
        name = skill_dir.name
        skill_file = safe_skill_file(skill_dir)
        if skill_file is None:
            self._record(
                scope,
                name,
                str(skill_dir),
                "SKILL.md is missing, a symlink, or escapes the skill root",
            )
            return None
        try:
            if skill_file.stat().st_size > MAX_SKILL_FILE_BYTES:
                raise SkillParseError(
                    f"SKILL.md exceeds {MAX_SKILL_FILE_BYTES} bytes"
                )
            text = skill_file.read_text(encoding="utf-8")
            parsed = parse_skill_document(text, expected_name=name)
        except (OSError, UnicodeError) as exc:
            self._record(scope, name, str(skill_dir), f"read error: {exc}")
            return None
        except SkillParseError as exc:
            self._record(scope, name, str(skill_dir), str(exc))
            return None
        return SkillMetadata(
            name=parsed.name,
            description=parsed.description,
            scope=scope,
            location=skill_file,
            license=parsed.license,
            compatibility=parsed.compatibility,
            metadata=parsed.metadata,
            allowed_tools=parsed.allowed_tools,
        )

    def _record(
        self,
        scope: SkillScope,
        name: str,
        location: str,
        reason: str,
    ) -> None:
        diagnostic = SkillDiagnostic(
            scope=scope,
            name=name,
            location=location,
            reason=reason,
        )
        self._diagnostics.append(diagnostic)
        logger.warning(diagnostic.render())


def safe_skill_dir(root: str | Path, name: str) -> Path | None:
    """返回位于 root 内、非符号链接的 Skill 目录；非法则返回 None。"""

    try:
        validate_skill_name(name)
    except ValueError:
        return None
    root_path = Path(root).expanduser().resolve()
    candidate = root_path / name
    if candidate.is_symlink():
        return None
    resolved = candidate.resolve()
    if not _is_within(root_path, resolved):
        return None
    if not resolved.is_dir():
        return None
    return resolved


def safe_skill_file(skill_dir: Path) -> Path | None:
    """返回 Skill 目录内的普通（非符号链接）SKILL.md；非法返回 None。"""

    skill_file = skill_dir / SKILL_FILE_NAME
    if skill_file.is_symlink():
        return None
    if not skill_file.is_file():
        return None
    resolved = skill_file.resolve()
    if not _is_within(skill_dir.resolve(), resolved):
        return None
    return skill_file


def safe_skill_resource(skill_dir: Path, relative: str) -> Path | None:
    """把 Skill 内相对资源路径安全解析为绝对路径；越界/符号链接返回 None。"""

    if not relative or relative.startswith(("/", "\\")):
        return None
    parts = Path(relative).parts
    if any(part in ("..", ".", "") for part in parts):
        return None
    root = skill_dir.resolve()
    raw_target = root / Path(*parts)
    if raw_target.is_symlink():
        return None
    target = raw_target.resolve()
    if not _is_within(root, target):
        return None
    if not target.is_file():
        return None
    return target


def _is_within(root: Path, target: Path) -> bool:
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


__all__ = [
    "DEFAULT_PROJECT_SKILLS_DIR",
    "DEFAULT_USER_SKILLS_DIR",
    "MAX_SKILL_FILE_BYTES",
    "SkillDiagnostic",
    "SkillDiscovery",
    "safe_skill_dir",
    "safe_skill_file",
    "safe_skill_resource",
]
