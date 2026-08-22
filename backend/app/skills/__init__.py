"""Skill Runtime：Agent Skills compatible progressive disclosure。

数据流：Discovery（metadata only）→ Context Catalog → 模型决定 → skill_read
激活（Run-scoped）→ Active Skill Context Provider（每个 Step 注入指令）→
执行时可安全读取 resources。

Skill 回答"以后遇到这种任务应该怎么做"。与 Task / Memory 边界：
- Task 回答"当前正在做什么"；
- Memory 回答"关于用户和过去未来还应知道什么"；
- Skill 回答"以后遇到这种任务应该怎么做"。
"""

from .config import SkillSettings
from .context import (
    ACTIVE_SKILL_MESSAGE_NAME,
    SKILL_CATALOG_MESSAGE_NAME,
    SkillContextProvider,
)
from .discovery import (
    DEFAULT_PROJECT_SKILLS_DIR,
    DEFAULT_USER_SKILLS_DIR,
    SkillDiagnostic,
    SkillDiscovery,
    safe_skill_dir,
    safe_skill_file,
    safe_skill_resource,
)
from .models import (
    SKILL_DESCRIPTION_MAX_LENGTH,
    SKILL_FILE_NAME,
    SKILL_NAME_MAX_LENGTH,
    Skill,
    SkillMetadata,
    SkillResources,
    SkillScope,
    valid_skill_name,
    validate_skill_name,
)
from .parser import ParsedSkill, SkillParseError, parse_skill_document
from .store import ManagedSkillEntry, SkillStore
from .tools import (
    SKILL_READ_TOOL_NAME,
    SKILL_RESOURCE_READ_TOOL_NAME,
    SkillReadTool,
    SkillResourceReadTool,
    register_skill_tools,
)

__all__ = [
    "ACTIVE_SKILL_MESSAGE_NAME",
    "DEFAULT_PROJECT_SKILLS_DIR",
    "DEFAULT_USER_SKILLS_DIR",
    "ParsedSkill",
    "SKILL_CATALOG_MESSAGE_NAME",
    "SKILL_DESCRIPTION_MAX_LENGTH",
    "SKILL_FILE_NAME",
    "SKILL_NAME_MAX_LENGTH",
    "SKILL_READ_TOOL_NAME",
    "SKILL_RESOURCE_READ_TOOL_NAME",
    "Skill",
    "SkillDiagnostic",
    "SkillDiscovery",
    "SkillMetadata",
    "ManagedSkillEntry",
    "SkillParseError",
    "SkillReadTool",
    "SkillResourceReadTool",
    "SkillResources",
    "SkillScope",
    "SkillSettings",
    "SkillStore",
    "SkillContextProvider",
    "parse_skill_document",
    "register_skill_tools",
    "safe_skill_dir",
    "safe_skill_file",
    "safe_skill_resource",
    "valid_skill_name",
    "validate_skill_name",
]
