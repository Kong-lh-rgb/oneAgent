"""本地工具的安全执行边界。

包含三道防线：
- 权限控制：ALLOWED 直接执行；HUMAN_APPROVAL 需人工审核；FORBIDDEN 拒绝。
- 资源限制：超时、输出截断、参数必须是 JSON 对象。
- 生命周期 Hook：权限、事件和可观测性通过统一执行阶段接入。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import replace
from time import perf_counter
from typing import Any

from app.models.types import ToolCall, ToolResult

from .approval import ApprovalDecision, ApprovalGate, DenyAllGate
from .base import BaseTool
from .hooks import ToolExecutionContext, ToolHook, ToolHookDecision, ToolHookRunner
from .observability import (
    InMemoryExecutionLogger,
    ObservabilityHook,
    ToolExecutionLogger,
    ToolExecutionRecord,
    _now_iso,
)
from .permission_hook import PermissionHook
from .permissions.policy import PermissionPolicyEngine
from .permissions.rule_factory import build_safe_rule
from .permissions.store import PermissionRuleStore
from .registry import ToolRegistry

MAX_TOOL_OUTPUT_CHARS = 20_000


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        timeout_seconds: float = 30.0,
        max_output_chars: int = MAX_TOOL_OUTPUT_CHARS,
        approval_gate: ApprovalGate | None = None,
        logger: ToolExecutionLogger | None = None,
        hooks: Sequence[ToolHook] = (),
        policy_engine: PermissionPolicyEngine | None = None,
        rule_store: PermissionRuleStore | None = None,
        rule_factory: Any = build_safe_rule,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if max_output_chars <= 0:
            raise ValueError("max_output_chars must be greater than zero")
        if max_output_chars > MAX_TOOL_OUTPUT_CHARS:
            raise ValueError(f"max_output_chars cannot exceed {MAX_TOOL_OUTPUT_CHARS}")
        if (
            policy_engine is not None
            and rule_store is not None
            and policy_engine.store is not rule_store
        ):
            raise ValueError("policy_engine and rule_store must use the same store")
        resolved_store = rule_store or (
            policy_engine.store if policy_engine is not None else None
        )
        resolved_policy = policy_engine or (
            PermissionPolicyEngine(resolved_store)
            if resolved_store is not None
            else None
        )
        self._registry = registry
        self._timeout_seconds = timeout_seconds
        self._max_output_chars = max_output_chars
        self.logger = logger or InMemoryExecutionLogger()
        self._permission_hook = PermissionHook(
            approval_gate or DenyAllGate(),
            policy=resolved_policy,
            rule_store=resolved_store,
            rule_factory=rule_factory,
        )
        self._hooks = (
            self._permission_hook,
            ObservabilityHook(self.logger),
            *hooks,
        )

    @property
    def execution_records(self) -> tuple[ToolExecutionRecord, ...]:
        """最近的执行记录（仅当使用 InMemoryExecutionLogger 时可用）。"""
        if isinstance(self.logger, InMemoryExecutionLogger):
            return self.logger.records
        return ()

    async def clear_run_rules(self, run_id: str) -> int:
        """清理一次 Agent Run 创建的临时审批规则。"""

        return await self._permission_hook.clear_run_rules(run_id)

    async def execute(
        self,
        tool_call: ToolCall,
        *,
        context: ToolExecutionContext | None = None,
        hooks: Sequence[ToolHook] = (),
    ) -> ToolResult:
        started_at = perf_counter()
        started_iso = _now_iso()

        tool = self._lookup_tool(tool_call)
        arguments = _safe_arguments(tool_call.arguments)
        base_context = context or ToolExecutionContext(tool_call=tool_call)
        execution_context = replace(
            base_context,
            tool_call=tool_call,
            tool_definition=tool.definition if tool is not None else None,
            arguments=arguments,
            metadata={**base_context.metadata, "started_at": started_iso},
        )
        hook_runner = ToolHookRunner(*self._hooks, *hooks)
        permission_check = await hook_runner.before_execute(execution_context)

        if tool is None:
            result = self._failure(
                tool_call,
                f"Tool not found: {tool_call.name}",
                started_at,
            )
            return await self._complete(execution_context, result, hook_runner)

        denied_reason = await self._authorize(
            execution_context,
            hook_runner,
            permission_check,
        )
        if denied_reason is not None:
            result = self._failure(tool_call, denied_reason, started_at)
            return await self._complete(execution_context, result, hook_runner)

        result = await self._dispatch(
            tool,
            tool_call,
            execution_context,
            started_at,
        )
        return await self._complete(execution_context, result, hook_runner)

    def _lookup_tool(self, tool_call: ToolCall) -> BaseTool | None:
        try:
            return self._registry.get(tool_call.name)
        except KeyError:
            return None

    async def _authorize(
        self,
        context: ToolExecutionContext,
        hook_runner: ToolHookRunner,
        check: ToolHookDecision | None,
    ) -> str | None:
        """返回被拒绝的原因；允许执行时返回 None。"""
        try:
            if check is None:
                return None
            if check.denied_reason is not None:
                return check.denied_reason
            if check.approval_request is None:
                return None

            request = check.approval_request

            # 规则命中：无需询问用户，直接放行并记录命中事实。
            if check.matched_rule is not None:
                await hook_runner.on_approval_completed(
                    context,
                    request,
                    ApprovalDecision.APPROVED,
                    rule=check.matched_rule,
                )
                return None

            await hook_runner.on_approval_required(context, request)
            outcome = await self._permission_hook.request_approval(
                request,
                context=context,
            )
            await hook_runner.on_approval_completed(
                context,
                request,
                outcome.response.decision,
                rule=outcome.rule,
            )
            return self._permission_hook.denied_reason(
                context,
                outcome.response.decision,
            )
        except Exception as exc:
            return f"Permission check failed: {type(exc).__name__}: {exc}"

    async def _dispatch(
        self,
        tool: BaseTool,
        tool_call: ToolCall,
        context: ToolExecutionContext,
        started_at: float,
    ) -> ToolResult:
        try:
            arguments = _parse_arguments(tool_call.arguments)
        except (TypeError, ValueError) as exc:
            return self._failure(
                tool_call,
                f"Invalid arguments: {exc}",
                started_at,
            )

        try:
            async with asyncio.timeout(self._timeout_seconds):
                output = await tool.execute_with_context(arguments, context)
        except TimeoutError:
            return self._failure(
                tool_call,
                f"Tool timed out after {self._timeout_seconds:g} seconds.",
                started_at,
            )
        except (KeyError, TypeError, ValueError) as exc:
            return self._failure(
                tool_call,
                f"Invalid arguments: {exc}",
                started_at,
            )
        except Exception as exc:
            return self._failure(
                tool_call,
                f"Tool execution failed: {type(exc).__name__}: {exc}",
                started_at,
            )

        return ToolResult(
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            success=True,
            output=_truncate(_serialize_output(output), self._max_output_chars),
            error=None,
            duration_ms=_duration_ms(started_at),
        )

    def _failure(
        self,
        tool_call: ToolCall,
        error: str,
        started_at: float,
    ) -> ToolResult:
        return ToolResult(
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            success=False,
            output=None,
            error=error,
            duration_ms=_duration_ms(started_at),
        )

    async def _complete(
        self,
        context: ToolExecutionContext,
        result: ToolResult,
        hook_runner: ToolHookRunner,
    ) -> ToolResult:
        """发送完成阶段并保持原始工具结果不受观察者影响。"""

        await hook_runner.after_execute(context, result)
        return result


def _parse_arguments(arguments: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(arguments, dict):
        return arguments
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError as exc:
        raise ValueError(f"arguments are not valid JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise TypeError("arguments must be a JSON object")
    return parsed


def _safe_arguments(arguments: dict[str, Any] | str) -> dict[str, Any]:
    """用于审批展示的参数；解析失败时返回空字典而不是抛错。"""
    try:
        return _parse_arguments(arguments)
    except (TypeError, ValueError):
        return {}


def _serialize_output(output: Any) -> str:
    if isinstance(output, str):
        return output
    try:
        return json.dumps(output, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(output)


def _truncate(value: str, limit: int) -> str:
    return value[:limit]


def _duration_ms(started_at: float) -> float:
    return max(0.0, (perf_counter() - started_at) * 1000)
