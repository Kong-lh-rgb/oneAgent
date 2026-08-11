"""基于可信运行上下文选择记忆 namespace。"""

from __future__ import annotations

from collections.abc import Sequence


class MemoryNamespaceRouter:
    """使用保守规则路由，模型不能自行填写任意 namespace。"""

    def route(
        self,
        user_text: str,
        *,
        allowed_namespaces: Sequence[str],
        default_namespace: str,
    ) -> str:
        allowed = tuple(dict.fromkeys(allowed_namespaces))
        if default_namespace not in allowed:
            raise ValueError("memory write namespace must be allowed for retrieval")
        normalized = user_text.lower()
        project_signals = ("项目", "仓库", "代码库", "oneagent", "project", "repo")
        if any(signal in normalized for signal in project_signals):
            project = next(
                (item for item in allowed if item.startswith("project:")),
                None,
            )
            if project is not None:
                return project
        return default_namespace


__all__ = ["MemoryNamespaceRouter"]
