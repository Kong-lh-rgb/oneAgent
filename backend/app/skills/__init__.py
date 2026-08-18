"""Skill：可复用的操作流程（Procedural Knowledge）。

Skill 回答"以后遇到这种任务应该怎么做"。每个 Skill 是一个带 Front Matter
的 Markdown 文件（``skills/<name>.md``），由开发者或用户维护；模型通过
``skill_list`` / ``skill_read`` 按需发现并加载，不自动注入 Prompt。

与 Task / Memory 的边界：
- Task 回答"当前正在做什么"；
- Memory 回答"关于用户和过去未来还应知道什么"；
- Skill 回答"以后遇到这种任务应该怎么做"。
"""

from .models import Skill, parse_skill_markdown
from .store import DEFAULT_SKILLS_DIR, SkillStore
from .tools import (
    SkillListTool,
    SkillReadTool,
    register_skill_tools,
)

__all__ = [
    "DEFAULT_SKILLS_DIR",
    "Skill",
    "SkillListTool",
    "SkillReadTool",
    "SkillStore",
    "parse_skill_markdown",
    "register_skill_tools",
]
