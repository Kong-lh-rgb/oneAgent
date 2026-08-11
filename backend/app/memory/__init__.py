"""长期记忆：Sparse, Model-Directed Long-Term Memory System。

设计要点：

- 持久化介质为 Markdown 文件（``CORE.md`` / ``INDEX.md`` / ``active/Mxxx.md``），
  不使用 SQLite / FTS / Embedding / Vector Search；
- 模型决定何时 Recall、创建、更新、归档（Model-directed recall with cues）；
- Runtime 只加载 Core Memory 与 Memory Index、暴露语义工具、维护元数据，
  不做 query-driven 自动检索或 Top-K 注入。
"""

from .core import DEFAULT_MAX_CORE_TOKENS, CoreMemoryManager
from .index import MemoryIndex
from .maintenance import MemoryMaintenance
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
from .store import DEFAULT_MEMORY_DIR, MemoryStore
from .tools import (
    MemoryArchiveTool,
    MemoryCreateTool,
    MemoryListTool,
    MemoryReadTool,
    MemoryUpdateTool,
    register_memory_tools,
)

__all__ = [
    "CORE_MEMORY_MESSAGE_NAME",
    "CoreMemoryManager",
    "DEFAULT_MAX_CORE_TOKENS",
    "DEFAULT_MEMORY_DIR",
    "MEMORY_INDEX_MESSAGE_NAME",
    "MEMORY_POLICY_MESSAGE_NAME",
    "MEMORY_POLICY_PROMPT",
    "MEMORY_WRITE_POLICY",
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
    "next_memory_id",
    "parse_memory_markdown",
    "register_memory_tools",
]
