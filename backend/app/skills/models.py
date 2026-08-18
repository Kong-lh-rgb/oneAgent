"""Skill 数据模型：轻量 Metadata 与完整定义分离。

目录式布局：

```text
skills/<name>/
├── SKILL.md         必选（Front Matter + 指令正文）
├── scripts/         可选
├── references/      可选
└── assets/          可选
```

Discovery 只建立轻量 ``SkillMetadata``；激活时才加载完整 ``Skill`` 正文。
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

# 小写字母/数字/单连字符；不以 - 开头或结尾；无连续 --。
_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SKILL_NAME_MAX_LENGTH = 64
SKILL_DESCRIPTION_MAX_LENGTH = 1024
SKILL_FILE_NAME = "SKILL.md"


class SkillScope(StrEnum):
    """Skill 的来源层级。"""

    USER = "user"
    PROJECT = "project"


def validate_skill_name(name: str) -> str:
    """校验 Skill name；非法时抛出 ValueError。"""

    normalized = name.strip()
    if not normalized:
        raise ValueError("skill name cannot be empty")
    if len(normalized) > SKILL_NAME_MAX_LENGTH:
        raise ValueError(
            f"skill name exceeds {SKILL_NAME_MAX_LENGTH} chars: {name!r}"
        )
    if not _SKILL_NAME_RE.fullmatch(normalized):
        raise ValueError(
            "skill name must be lowercase letters/digits separated by single "
            f"hyphens: {name!r}"
        )
    return normalized


def valid_skill_name(name: str) -> bool:
    """非抛出式校验。"""

    try:
        validate_skill_name(name)
        return True
    except ValueError:
        return False


class SkillMetadata(BaseModel):
    """Skill 的轻量目录项（Discovery 阶段建立，不包含正文）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    description: str
    scope: SkillScope
    location: Path
    license: str | None = None
    compatibility: str | None = None
    metadata: dict[str, object] | None = None
    # TODO(skill-allowed-tools): 尚未参与工具权限。未来只允许收窄当前 Run 的
    # 工具集合（不能把 approval 提升成 allowed，也不能解禁 forbidden）；
    # 在 Permission/ToolExecutor 支持该不变量前，保持"只解析、不生效"。
    allowed_tools: tuple[str, ...] = ()

    def render_catalog_entry(self) -> str:
        """渲染为注入模型上下文的精简目录项（仅 name + description）。"""

        return f"[{self.name}] {self.description}"


class SkillResources(BaseModel):
    """Skill 目录内可安全访问的资源清单（仅相对路径，不加载正文）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scripts: tuple[str, ...] = ()
    references: tuple[str, ...] = ()
    assets: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, tuple[str, ...]]:
        return {
            "references": self.references,
            "scripts": self.scripts,
            "assets": self.assets,
        }

    def is_empty(self) -> bool:
        return not (self.scripts or self.references or self.assets)


class Skill(BaseModel):
    """激活后的完整 Skill（正文 + 资源清单 + 来源根目录）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metadata: SkillMetadata
    content: str
    root: Path
    resources: SkillResources = Field(default_factory=SkillResources)

    def render_instructions(self) -> str:
        """渲染为注入模型上下文的 Active Skill 指令块。"""

        header = f"# Skill: {self.metadata.name}"
        body = [
            header,
            "",
            self.content.strip(),
        ]
        if not self.resources.is_empty():
            body.extend(
                (
                    "",
                    "## Resources",
                    "",
                    "可用的资源（需要时用 skill_resource_read 读取，不自动加载）：",
                )
            )
            for kind, items in self.resources.as_dict().items():
                if items:
                    body.append(f"- {kind}: " + ", ".join(items))
        return "\n".join(body)


__all__ = [
    "SKILL_DESCRIPTION_MAX_LENGTH",
    "SKILL_FILE_NAME",
    "SKILL_NAME_MAX_LENGTH",
    "Skill",
    "SkillMetadata",
    "SkillResources",
    "SkillScope",
    "valid_skill_name",
    "validate_skill_name",
]
