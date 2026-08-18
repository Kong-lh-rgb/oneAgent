"""Skill 数据模型。

每个 Skill 是一个带 YAML Front Matter 的 Markdown 文件：

```text
skills/<name>.md
```

Front Matter 保存 ``name`` 与 ``description``；正文是可复用的操作流程。
``description`` 用于模型判断何时使用该 Skill（类似工具定义的语义）。
"""

from __future__ import annotations

import yaml
from pydantic import BaseModel, ConfigDict, Field


class Skill(BaseModel):
    """一条可复用的操作流程（Procedural Knowledge）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    description: str
    content: str


def parse_skill_markdown(text: str) -> Skill:
    """从 Markdown 文件内容解析 Skill；Front Matter 为权威元数据。"""

    front, body = _split_front_matter(text)
    if front is None:
        raise ValueError("skill file is missing YAML front matter")
    data = yaml.safe_load(front)
    if not isinstance(data, dict):
        raise ValueError("skill front matter must be a mapping")
    name = str(data["name"]).strip()
    description = str(data.get("description", "")).strip()
    content = body.strip()
    if not content:
        raise ValueError("skill content cannot be empty")
    return Skill(name=name, description=description, content=content)


def _split_front_matter(text: str) -> tuple[str | None, str]:
    """把 ``---`` 包裹的 Front Matter 与正文分开。"""

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join(lines[1:index]), "\n".join(lines[index + 1 :]).strip()
    return None, text


__all__ = ["Skill", "parse_skill_markdown"]
