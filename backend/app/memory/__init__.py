"""长期记忆：Sparse, Model-Directed Long-Term Memory System。

设计要点：

- 持久化介质为 Markdown 文件（``CORE.md`` / ``INDEX.md`` / ``active/Mxxx.md``），
  不使用 SQLite / FTS / Embedding / Vector Search；
- Main Agent 决定何时 Recall，并只处理用户明确要求的 Core 变更；
- 普通 Memory 的创建或更新由正常 Run 后的独立 Reflection 模型决定；
- Runtime 加载 Core Memory 与 Memory Index、维护元数据，不做 query-driven
  自动检索或 Top-K 注入。
"""

from .core import DEFAULT_MAX_CORE_TOKENS, CoreMemoryEntry, CoreMemoryManager
from .index import MemoryIndex
from .maintenance import MemoryMaintenance
from .maintenance_models import (
    MaintenanceAction,
    MemoryMaintenanceCandidate,
    MemoryMaintenanceConfig,
    MemoryMaintenanceDecision,
    MemoryMaintenanceInput,
    MemoryMaintenanceProposal,
)
from .maintenance_reflection import MemoryMaintenanceReflector
from .manager import (
    CORE_MEMORY_MESSAGE_NAME,
    MEMORY_INDEX_MESSAGE_NAME,
    MEMORY_POLICY_MESSAGE_NAME,
    MemoryManager,
)
from .models import (
    MemoryRecord,
    MemoryStatus,
    next_memory_id,
    parse_memory_markdown,
)
from .prompts import MEMORY_POLICY_PROMPT, MEMORY_WRITE_POLICY
from .reflection import PostRunMemoryReflector
from .reflection_models import (
    MemoryReflectionConfig,
    MemoryReflectionInput,
    MemoryReflectionProposal,
    ReflectionAction,
    ReflectionDecision,
)
from .store import DEFAULT_MEMORY_DIR, MemoryStore
from .tools import (
    CoreMemoryRemoveTool,
    CoreMemoryUpdateTool,
    MemoryArchiveTool,
    MemoryCreateTool,
    MemoryListTool,
    MemoryReadTool,
    MemoryUpdateTool,
    register_memory_tools,
    register_memory_write_tools,
)

__all__ = [
    "CORE_MEMORY_MESSAGE_NAME",
    "CoreMemoryEntry",
    "CoreMemoryManager",
    "CoreMemoryRemoveTool",
    "CoreMemoryUpdateTool",
    "DEFAULT_MAX_CORE_TOKENS",
    "DEFAULT_MEMORY_DIR",
    "MEMORY_INDEX_MESSAGE_NAME",
    "MEMORY_POLICY_MESSAGE_NAME",
    "MEMORY_POLICY_PROMPT",
    "MEMORY_WRITE_POLICY",
    "MaintenanceAction",
    "MemoryMaintenanceCandidate",
    "MemoryMaintenanceConfig",
    "MemoryMaintenanceDecision",
    "MemoryMaintenanceInput",
    "MemoryMaintenanceProposal",
    "MemoryMaintenanceReflector",
    "MemoryReflectionConfig",
    "MemoryReflectionInput",
    "MemoryReflectionProposal",
    "MemoryArchiveTool",
    "MemoryCreateTool",
    "MemoryIndex",
    "MemoryListTool",
    "MemoryMaintenance",
    "MemoryManager",
    "MemoryReadTool",
    "MemoryRecord",
    "MemoryStatus",
    "MemoryStore",
    "MemoryUpdateTool",
    "PostRunMemoryReflector",
    "ReflectionAction",
    "ReflectionDecision",
    "next_memory_id",
    "parse_memory_markdown",
    "register_memory_tools",
    "register_memory_write_tools",
]
