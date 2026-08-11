"""OneAgent 长期记忆 V1 的冻结实现。

V1 当前不再由 CLI 装配运行，仅保留领域模型、存储和检索代码作为后续重构的
设计样本。新架构确定前不要继续扩展这套候选审批式流程。
"""

from .config import MemorySettings
from .embedder import (
    HashMemoryEmbedder,
    MemoryEmbedder,
    OpenAICompatibleMemoryEmbedder,
)
from .errors import MemoryConflictError, MemoryRevisionConflictError, MemoryStoreError
from .extractor import MemoryExtractor, ModelMemoryExtractor, RuleMemoryFilter
from .manager import MEMORY_CONTEXT_MESSAGE_NAME, MemoryManager
from .models import (
    MemoryDraft,
    MemoryItem,
    MemorySearchResult,
    MemorySource,
    MemoryStatus,
    MemoryType,
)
from .retriever import HybridMemoryRetriever
from .router import MemoryNamespaceRouter
from .store import SQLiteMemoryStore
from .writer import (
    MemoryWriteAction,
    MemoryWriteBudget,
    MemoryWriter,
    MemoryWriteResult,
)

__all__ = [
    "MEMORY_CONTEXT_MESSAGE_NAME",
    "HashMemoryEmbedder",
    "HybridMemoryRetriever",
    "MemoryConflictError",
    "MemoryDraft",
    "MemoryEmbedder",
    "MemoryExtractor",
    "MemoryItem",
    "MemoryManager",
    "MemoryNamespaceRouter",
    "MemoryRevisionConflictError",
    "MemorySearchResult",
    "MemorySettings",
    "MemorySource",
    "MemoryStatus",
    "MemoryStoreError",
    "MemoryType",
    "MemoryWriteAction",
    "MemoryWriteBudget",
    "MemoryWriteResult",
    "MemoryWriter",
    "ModelMemoryExtractor",
    "OpenAICompatibleMemoryEmbedder",
    "RuleMemoryFilter",
    "SQLiteMemoryStore",
]
