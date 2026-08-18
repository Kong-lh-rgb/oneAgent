"""Skill 工具：激活与资源读取。

- ``skill_read(name)``：激活并读取完整 Skill（正文 + 资源清单）；激活状态
  由 Runtime 维护（Run-scoped），后续每个 Step 自动注入指令；
- ``skill_resource_read(name, path)``：安全读取 Skill 目录内的资源正文，
  路径严格限制在 Skill 根目录内（防 ../ / 绝对路径 / 符号链接逃逸）。

Skill Catalog（name + description）由 Context Provider 自动注入，因此不再
需要独立的 ``skill_list`` 工具。
"""

from __future__ import annotations

from typing import Any

from app.models.types import ToolDefinition

from ..tools.base import BaseTool
from ..tools.registry import ToolRegistry
from .discovery import safe_skill_resource
from .store import SkillStore

SKILL_READ_TOOL_NAME = "skill_read"
SKILL_RESOURCE_READ_TOOL_NAME = "skill_resource_read"

_MAX_RESOURCE_BYTES = 64_000


class SkillReadTool(BaseTool):
    """激活并读取一个 Skill 的完整指令。"""

    def __init__(self, store: SkillStore) -> None:
        self._store = store

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="skill_read",
            description=(
                "激活并读取一个 Skill 的完整操作流程。只有 Available Skills "
                "目录中出现当前任务匹配的 Skill 时才调用；激活后其指令会在本 "
                "Run 内持续生效。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "要激活的 Skill 名称（Available Skills 目录中的 name）。"
                        ),
                    },
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            strict=False,
        )

    async def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        name = arguments.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("'name' must be a non-empty string")
        skill = await self._store.load(name)
        if skill is None:
            return {"found": False, "name": name}
        return {
            "found": True,
            "name": skill.metadata.name,
            "description": skill.metadata.description,
            "scope": skill.metadata.scope.value,
            "content": skill.content,
            "resources": skill.resources.as_dict(),
        }


class SkillResourceReadTool(BaseTool):
    """安全读取 Skill 目录内的资源（references/scripts/assets）。"""

    def __init__(self, store: SkillStore) -> None:
        self._store = store

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="skill_resource_read",
            description=(
                "读取当前激活 Skill 目录内的一个资源文件（Skill 激活时返回的 "
                "references/scripts/assets 清单中的相对路径）。路径必须位于该 "
                "Skill 目录内，禁止 ../ 或绝对路径。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Skill 名称。",
                    },
                    "path": {
                        "type": "string",
                        "description": "Skill 内资源的相对路径。",
                    },
                },
                "required": ["name", "path"],
                "additionalProperties": False,
            },
            strict=False,
        )

    async def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        name = arguments.get("name")
        resource_path = arguments.get("path")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("'name' must be a non-empty string")
        if not isinstance(resource_path, str) or not resource_path.strip():
            raise ValueError("'path' must be a non-empty string")
        skill = await self._store.load(name)
        if skill is None:
            return {"found": False, "name": name}
        target = safe_skill_resource(skill.root, resource_path)
        if target is None:
            return {
                "found": False,
                "name": name,
                "path": resource_path,
                "error": "resource path escapes skill root or is not a file",
            }
        try:
            if target.stat().st_size > _MAX_RESOURCE_BYTES:
                return {
                    "found": False,
                    "name": name,
                    "path": resource_path,
                    "error": f"resource exceeds {_MAX_RESOURCE_BYTES} bytes",
                }
            content = target.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return {
                "found": False,
                "name": name,
                "path": resource_path,
                "error": "resource read failed",
            }
        return {
            "found": True,
            "name": name,
            "path": resource_path,
            "content": content,
        }


def register_skill_tools(
    registry: ToolRegistry,
    store: SkillStore,
) -> None:
    """把 Skill 工具注册到本地工具注册表。"""

    registry.register(SkillReadTool(store))
    registry.register(SkillResourceReadTool(store))


__all__ = [
    "SKILL_READ_TOOL_NAME",
    "SKILL_RESOURCE_READ_TOOL_NAME",
    "SkillReadTool",
    "SkillResourceReadTool",
    "register_skill_tools",
]
