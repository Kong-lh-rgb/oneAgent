"""可记忆的人工审批规则子系统。"""

from .matchers import (
    ExactArgumentsMatcher,
    PermissionMatcher,
    build_matcher,
)
from .models import (
    ApprovalResponse,
    ApprovalScope,
    PermissionEffect,
    PermissionRule,
    PermissionVerdict,
)
from .policy import PermissionPolicyEngine
from .rule_factory import build_safe_rule, describe_safe_rule
from .store import (
    InMemoryPermissionRuleStore,
    PermissionRuleStore,
    SQLitePermissionRuleStore,
)

__all__ = [
    "ApprovalResponse",
    "ApprovalScope",
    "ExactArgumentsMatcher",
    "InMemoryPermissionRuleStore",
    "PermissionEffect",
    "PermissionMatcher",
    "PermissionPolicyEngine",
    "PermissionRule",
    "PermissionRuleStore",
    "PermissionVerdict",
    "SQLitePermissionRuleStore",
    "build_matcher",
    "build_safe_rule",
    "describe_safe_rule",
]
