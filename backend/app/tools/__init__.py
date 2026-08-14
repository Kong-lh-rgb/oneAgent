"""本地异步工具系统。"""

from .approval import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalRequest,
    ApprovalResponse,
    ApprovalScope,
    AutoApproveGate,
    ConsoleApprovalGate,
    DenyAllGate,
)
from .base import BaseTool
from .builtin import (
    CurrentTimeTool,
    HttpRequestTool,
    ListFilesTool,
    ReadFileTool,
    ShellCommandTool,
    WebSearchTool,
    WriteFileTool,
    build_builtin_tool_registry,
)
from .catalog import (
    TOOL_SEARCH_NAME,
    ToolCatalog,
    ToolCatalogMatch,
    ToolSearchTool,
)
from .executor import MAX_TOOL_OUTPUT_CHARS, ToolExecutor
from .hooks import ToolExecutionContext, ToolHook, ToolHookDecision, ToolHookRunner
from .observability import (
    InMemoryExecutionLogger,
    ObservabilityHook,
    StructLogExecutionLogger,
    ToolExecutionLogger,
    ToolExecutionRecord,
)
from .permission_hook import PermissionHook
from .permissions import (
    InMemoryPermissionRuleStore,
    PermissionEffect,
    PermissionMatcher,
    PermissionPolicyEngine,
    PermissionRule,
    PermissionRuleStore,
    PermissionVerdict,
    SQLitePermissionRuleStore,
    build_matcher,
    build_safe_rule,
    describe_safe_rule,
)
from .registry import ToolRegistry

__all__ = [
    "ApprovalDecision",
    "ApprovalGate",
    "ApprovalRequest",
    "ApprovalResponse",
    "ApprovalScope",
    "AutoApproveGate",
    "BaseTool",
    "ConsoleApprovalGate",
    "CurrentTimeTool",
    "DenyAllGate",
    "HttpRequestTool",
    "InMemoryExecutionLogger",
    "InMemoryPermissionRuleStore",
    "ListFilesTool",
    "MAX_TOOL_OUTPUT_CHARS",
    "ObservabilityHook",
    "PermissionEffect",
    "PermissionHook",
    "PermissionMatcher",
    "PermissionPolicyEngine",
    "PermissionRule",
    "PermissionRuleStore",
    "PermissionVerdict",
    "ReadFileTool",
    "SQLitePermissionRuleStore",
    "ShellCommandTool",
    "StructLogExecutionLogger",
    "TOOL_SEARCH_NAME",
    "ToolCatalog",
    "ToolCatalogMatch",
    "ToolSearchTool",
    "ToolExecutionLogger",
    "ToolExecutionRecord",
    "ToolExecutionContext",
    "ToolExecutor",
    "ToolHook",
    "ToolHookDecision",
    "ToolHookRunner",
    "ToolRegistry",
    "WebSearchTool",
    "WriteFileTool",
    "build_builtin_tool_registry",
    "build_matcher",
    "build_safe_rule",
    "describe_safe_rule",
]
