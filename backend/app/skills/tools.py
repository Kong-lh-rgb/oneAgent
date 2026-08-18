"""Skill 工具：模型按需发现并加载可复用流程。

Skill 是"以后遇到这种任务应该怎么做"的程序性知识。模型在当前任务匹配某个
Skill 的 description 时，先 ``skill_list`` 发现，再 ``skill_read`` 加载完整
流程并遵循执行。
"""

from __future__ import annotations

from typing import Any

from app.models.types import ToolDefinition

from ..tools.base import BaseTool
from ..tools.registry import ToolRegistry
from .store import SkillStore


class SkillListTool(BaseTool):
    """列出所有可用的 Skill。"""

    def __init__(self, store: SkillStore) -> None:
        self._store = store

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="skill_list",
            description=(
                "列出所有可用的 Skill（名称与用途说明）。当当前任务属于某种"
                "已有成熟流程（例如调试某类问题、部署、特定编码工作流）时，"
                "先调用本工具查找是否有可复用的 Skill，再用 skill_read 加载。"
            ),
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            strict=False,
        )

    async def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        skills = await self._store.list()
        return {
            "skills": [
                {
                    "name": skill.name,
                    "description": skill.description,
                }
                for skill in skills
            ]
        }


class SkillReadTool(BaseTool):
    """读取一个 Skill 的完整操作流程。"""

    def __init__(self, store: SkillStore) -> None:
        self._store = store

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="skill_read",
            description=(
                "读取一个 Skill 的完整操作流程。只在 skill_list 显示当前任务"
                "匹配某个 Skill 时调用，随后严格按流程执行。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "要读取的 Skill 名称（skill_list 返回的 name）。"
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
            "name": skill.name,
            "description": skill.description,
            "content": skill.content,
        }


def register_skill_tools(
    registry: ToolRegistry,
    store: SkillStore,
) -> None:
    """把 Skill 工具注册到本地工具注册表。"""

    registry.register(SkillListTool(store))
    registry.register(SkillReadTool(store))


__all__ = [
    "SkillListTool",
    "SkillReadTool",
    "register_skill_tools",
]
