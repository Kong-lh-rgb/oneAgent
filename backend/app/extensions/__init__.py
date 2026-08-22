"""外部扩展导入：只解析受支持格式，不执行粘贴内容中的任意命令。"""

from .importer import (
    ExtensionImportError,
    ExtensionImportPlan,
    apply_import_plan,
    parse_import_plan,
)

__all__ = [
    "ExtensionImportError",
    "ExtensionImportPlan",
    "apply_import_plan",
    "parse_import_plan",
]
