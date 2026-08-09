"""记忆存储的明确错误类型。"""


class MemoryStoreError(Exception):
    """记忆存储错误的基类。"""


class MemoryConflictError(MemoryStoreError, ValueError):
    """同作用域中存在相同规范键的有效记忆。"""


class MemoryRevisionConflictError(MemoryStoreError, ValueError):
    """调用方基于过期 revision 修改记忆。"""


__all__ = [
    "MemoryConflictError",
    "MemoryRevisionConflictError",
    "MemoryStoreError",
]
