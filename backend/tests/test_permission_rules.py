"""可记忆的人工审批规则：匹配器、规则工厂、策略引擎、存储与执行器集成。"""

from __future__ import annotations

from typing import Any

import pytest

from app.models.types import ToolCall, ToolDefinition, ToolPermission
from app.tools import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalRequest,
    ApprovalResponse,
    ApprovalScope,
    BaseTool,
    InMemoryPermissionRuleStore,
    PermissionEffect,
    PermissionPolicyEngine,
    PermissionRule,
    SQLitePermissionRuleStore,
    ToolExecutor,
    ToolRegistry,
    build_matcher,
    build_safe_rule,
    describe_safe_rule,
)
from app.tools.hooks import ToolExecutionContext, ToolHook
from app.tools.permissions.matchers import ExactArgumentsMatcher


class ScriptedGate(ApprovalGate):
    """按顺序返回预设的审批响应；耗尽后默认拒绝。"""

    def __init__(self, *responses: ApprovalResponse) -> None:
        self.responses = list(responses)
        self.calls = 0

    async def request_approval(self, request: ApprovalRequest) -> ApprovalResponse:
        self.calls += 1
        if not self.responses:
            return ApprovalResponse(decision=ApprovalDecision.DENIED)
        return self.responses.pop(0)


class ShellStub(BaseTool):
    """模拟 HUMAN_APPROVAL 的 shell 工具，只关心 command 参数。"""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="run_shell_command",
            description="run a shell command",
            permission=ToolPermission.HUMAN_APPROVAL,
        )

    def __init__(self) -> None:
        self.executions = 0

    async def execute(self, arguments: dict[str, Any]) -> str:
        self.executions += 1
        return f"ran:{arguments.get('command')}"


class RecordingApprovalHook(ToolHook):
    def __init__(self) -> None:
        self.completed: list[tuple[Any, Any]] = []

    async def on_approval_completed(
        self,
        context: ToolExecutionContext,
        request: ApprovalRequest,
        decision: ApprovalDecision,
        rule: Any = None,
    ) -> None:
        self.completed.append((decision, rule))


# ---- 匹配器 ----

def test_exact_arguments_matcher() -> None:
    matcher = ExactArgumentsMatcher({"command": "pytest tests"})

    assert matcher.matches({"command": "pytest tests"})
    assert not matcher.matches({"command": "pytest other"})
    assert not matcher.matches({})


def test_build_matcher_dispatches_by_type() -> None:
    assert isinstance(
        build_matcher("exact_arguments", {"arguments": {}}),
        ExactArgumentsMatcher,
    )
    # 旧版宽泛规则必须失效，避免命令拼接和 HTTP 权限扩大。
    with pytest.raises(ValueError, match="Unknown matcher"):
        build_matcher("command_prefix", {"prefix": "pytest"})
    with pytest.raises(ValueError, match="Unknown matcher"):
        build_matcher("host_exact", {"host": "example.com"})
    with pytest.raises(ValueError, match="Unknown matcher"):
        build_matcher("nope", {})


# ---- 规则工厂 ----

def test_rule_factory_run_scope_uses_exact_arguments() -> None:
    rule = build_safe_rule(
        tool_name="run_shell_command",
        arguments={"command": "pytest tests"},
        scope=ApprovalScope.RUN,
        scope_id="run-1",
    )

    assert rule.scope is ApprovalScope.RUN
    assert rule.scope_id == "run-1"
    assert rule.matcher_type == "exact_arguments"
    assert rule.matcher == {"arguments": {"command": "pytest tests"}}
    assert rule.effect is PermissionEffect.ALLOW


def test_rule_factory_conversation_shell_uses_exact_arguments() -> None:
    rule = build_safe_rule(
        tool_name="run_shell_command",
        arguments={"command": "pytest tests/test_runtime.py"},
        scope=ApprovalScope.CONVERSATION,
        scope_id="conv-1",
    )

    assert rule.matcher_type == "exact_arguments"
    assert rule.matcher == {"arguments": {"command": "pytest tests/test_runtime.py"}}


def test_rule_factory_conversation_http_uses_exact_arguments() -> None:
    rule = build_safe_rule(
        tool_name="http_request",
        arguments={"url": "https://example.com/a"},
        scope=ApprovalScope.CONVERSATION,
        scope_id="conv-1",
    )

    assert rule.matcher_type == "exact_arguments"
    assert rule.matcher == {
        "arguments": {"url": "https://example.com/a"}
    }


def test_describe_safe_rule() -> None:
    shell = ApprovalRequest(
        tool_call_id="1",
        tool_name="run_shell_command",
        arguments={"command": "pytest x"},
    )
    http = ApprovalRequest(
        tool_call_id="2",
        tool_name="http_request",
        arguments={"url": "https://a.com/"},
    )
    other = ApprovalRequest(tool_call_id="3", tool_name="other", arguments={})

    expected = "当前会话记住该操作（仅完整参数相同时自动通过）"
    assert describe_safe_rule(shell) == expected
    assert describe_safe_rule(http) == expected
    assert describe_safe_rule(other) == expected


def test_once_scope_cannot_be_persisted_as_rule() -> None:
    with pytest.raises(ValueError, match="ONCE"):
        PermissionRule(
            id="rule-once",
            tool_name="run_shell_command",
            scope=ApprovalScope.ONCE,
            scope_id="run-1",
            matcher_type="exact_arguments",
            matcher={"arguments": {"command": "pytest x"}},
            description="非法临时规则",
        )


# ---- 策略引擎 ----

@pytest.mark.asyncio
async def test_policy_engine_allows_matching_rule_in_scope() -> None:
    store = InMemoryPermissionRuleStore()
    rule = build_safe_rule(
        tool_name="run_shell_command",
        arguments={"command": "pytest x"},
        scope=ApprovalScope.CONVERSATION,
        scope_id="conv-1",
    )
    await store.add(rule)
    engine = PermissionPolicyEngine(store)

    verdict = await engine.evaluate(
        tool_name="run_shell_command",
        arguments={"command": "pytest x"},
        scope_ids=("conv-1",),
    )

    assert verdict.effect is PermissionEffect.ALLOW
    assert verdict.rule_id == rule.id


@pytest.mark.asyncio
async def test_policy_engine_asks_when_no_rule_matches() -> None:
    engine = PermissionPolicyEngine(InMemoryPermissionRuleStore())

    verdict = await engine.evaluate(
        tool_name="run_shell_command",
        arguments={"command": "pytest x"},
        scope_ids=("run-1",),
    )

    assert verdict.effect is PermissionEffect.ASK
    assert verdict.rule_id is None


@pytest.mark.asyncio
async def test_policy_engine_ignores_rules_outside_scope() -> None:
    store = InMemoryPermissionRuleStore()
    await store.add(
        build_safe_rule(
            tool_name="run_shell_command",
            arguments={"command": "pytest x"},
            scope=ApprovalScope.CONVERSATION,
            scope_id="conv-1",
        )
    )
    engine = PermissionPolicyEngine(store)

    verdict = await engine.evaluate(
        tool_name="run_shell_command",
        arguments={"command": "pytest x"},
        scope_ids=("other-conv",),
    )

    assert verdict.effect is PermissionEffect.ASK


@pytest.mark.asyncio
async def test_policy_engine_deny_rule_wins_over_allow_rule() -> None:
    store = InMemoryPermissionRuleStore()
    arguments = {"command": "pytest x"}
    await store.add(
        build_safe_rule(
            tool_name="run_shell_command",
            arguments=arguments,
            scope=ApprovalScope.CONVERSATION,
            scope_id="conv-1",
            effect=PermissionEffect.ALLOW,
        )
    )
    deny_rule = build_safe_rule(
        tool_name="run_shell_command",
        arguments=arguments,
        scope=ApprovalScope.CONVERSATION,
        scope_id="conv-1",
        effect=PermissionEffect.DENY,
    )
    await store.add(deny_rule)

    verdict = await PermissionPolicyEngine(store).evaluate(
        tool_name="run_shell_command",
        arguments=arguments,
        scope_ids=("conv-1",),
    )

    assert verdict.effect is PermissionEffect.DENY
    assert verdict.rule_id == deny_rule.id


@pytest.mark.asyncio
async def test_http_conversation_rule_does_not_expand_method_or_path() -> None:
    store = InMemoryPermissionRuleStore()
    await store.add(
        build_safe_rule(
            tool_name="http_request",
            arguments={
                "url": "https://api.example.com/status",
                "method": "GET",
            },
            scope=ApprovalScope.CONVERSATION,
            scope_id="conv-1",
        )
    )
    engine = PermissionPolicyEngine(store)

    same = await engine.evaluate(
        tool_name="http_request",
        arguments={
            "url": "https://api.example.com/status",
            "method": "GET",
        },
        scope_ids=("conv-1",),
    )
    changed = await engine.evaluate(
        tool_name="http_request",
        arguments={
            "url": "https://api.example.com/delete",
            "method": "POST",
        },
        scope_ids=("conv-1",),
    )

    assert same.effect is PermissionEffect.ALLOW
    assert changed.effect is PermissionEffect.ASK


# ---- 执行器集成：记住规则后不再询问 ----

def _shell_context(
    call_id: str,
    run_id: str,
    conversation_id: str,
) -> ToolExecutionContext:
    return ToolExecutionContext(
        tool_call=ToolCall(
            id=call_id,
            name="run_shell_command",
            arguments={"command": "pytest x"},
        ),
        run_id=run_id,
        conversation_id=conversation_id,
    )


@pytest.mark.asyncio
async def test_run_scope_rule_remembers_exact_operation() -> None:
    tool = ShellStub()
    registry = ToolRegistry()
    registry.register(tool)
    store = InMemoryPermissionRuleStore()
    engine = PermissionPolicyEngine(store)
    gate = ScriptedGate(
        ApprovalResponse(
            decision=ApprovalDecision.APPROVED,
            scope=ApprovalScope.RUN,
        )
    )
    executor = ToolExecutor(
        registry,
        approval_gate=gate,
        policy_engine=engine,
        rule_store=store,
    )
    context = _shell_context("c1", "run-1", "conv-1")

    first = await executor.execute(
        ToolCall(id="c1", name="run_shell_command", arguments={"command": "pytest x"}),
        context=context,
    )
    assert first.success is True
    assert gate.calls == 1

    # 相同操作：规则命中，不再询问
    second = await executor.execute(
        ToolCall(id="c2", name="run_shell_command", arguments={"command": "pytest x"}),
        context=context,
    )
    assert second.success is True
    assert gate.calls == 1
    assert tool.executions == 2

    # 不同参数：仍需询问（响应耗尽 → 拒绝）
    third = await executor.execute(
        ToolCall(id="c3", name="run_shell_command", arguments={"command": "pytest y"}),
        context=context,
    )
    assert third.success is False
    assert gate.calls == 2


@pytest.mark.asyncio
async def test_conversation_scope_only_allows_same_operation() -> None:
    tool = ShellStub()
    registry = ToolRegistry()
    registry.register(tool)
    store = InMemoryPermissionRuleStore()
    engine = PermissionPolicyEngine(store)
    gate = ScriptedGate(
        ApprovalResponse(
            decision=ApprovalDecision.APPROVED,
            scope=ApprovalScope.CONVERSATION,
        )
    )
    executor = ToolExecutor(
        registry,
        approval_gate=gate,
        policy_engine=engine,
        rule_store=store,
    )
    context = _shell_context("c1", "run-1", "conv-1")

    first = await executor.execute(
        ToolCall(id="c1", name="run_shell_command", arguments={"command": "pytest x"}),
        context=context,
    )
    assert first.success is True
    assert gate.calls == 1

    # 同一会话、新 Run、完整参数相同：规则命中。
    second = await executor.execute(
        ToolCall(
            id="c2",
            name="run_shell_command",
            arguments={"command": "pytest x"},
        ),
        context=_shell_context("c2", "run-2", "conv-1"),
    )
    assert second.success is True
    assert gate.calls == 1

    # 即使仍以 pytest 开头，只要加入拼接命令就必须再次询问并被拒绝。
    third = await executor.execute(
        ToolCall(
            id="c3",
            name="run_shell_command",
            arguments={"command": "pytest x; rm -rf /tmp/x"},
        ),
        context=_shell_context("c3", "run-3", "conv-1"),
    )
    assert third.success is False
    assert gate.calls == 2


@pytest.mark.asyncio
async def test_once_scope_does_not_create_rule() -> None:
    tool = ShellStub()
    registry = ToolRegistry()
    registry.register(tool)
    store = InMemoryPermissionRuleStore()
    engine = PermissionPolicyEngine(store)
    gate = ScriptedGate(ApprovalResponse(decision=ApprovalDecision.APPROVED))
    executor = ToolExecutor(
        registry,
        approval_gate=gate,
        policy_engine=engine,
        rule_store=store,
    )
    context = _shell_context("c1", "run-1", "conv-1")

    first = await executor.execute(
        ToolCall(id="c1", name="run_shell_command", arguments={"command": "pytest x"}),
        context=context,
    )
    assert first.success is True
    assert await store.list() == ()  # ONCE 不保存规则

    # 相同操作第二次仍询问
    second = await executor.execute(
        ToolCall(id="c2", name="run_shell_command", arguments={"command": "pytest x"}),
        context=context,
    )
    assert second.success is False
    assert gate.calls == 2


@pytest.mark.asyncio
async def test_approval_completed_event_carries_rule_id() -> None:
    tool = ShellStub()
    registry = ToolRegistry()
    registry.register(tool)
    store = InMemoryPermissionRuleStore()
    engine = PermissionPolicyEngine(store)
    gate = ScriptedGate(
        ApprovalResponse(
            decision=ApprovalDecision.APPROVED,
            scope=ApprovalScope.RUN,
        )
    )
    executor = ToolExecutor(
        registry,
        approval_gate=gate,
        policy_engine=engine,
        rule_store=store,
    )
    hook = RecordingApprovalHook()
    context = _shell_context("c1", "run-1", "conv-1")

    # 第一次：审批创建规则并带出规则
    await executor.execute(
        ToolCall(id="c1", name="run_shell_command", arguments={"command": "pytest x"}),
        context=context,
        hooks=(hook,),
    )
    assert len(hook.completed) == 1
    decision, rule = hook.completed[0]
    assert decision is ApprovalDecision.APPROVED
    assert rule is not None and rule.scope is ApprovalScope.RUN

    # 第二次：规则命中路径也触发 completed，且带规则
    await executor.execute(
        ToolCall(id="c2", name="run_shell_command", arguments={"command": "pytest x"}),
        context=context,
        hooks=(hook,),
    )
    assert len(hook.completed) == 2
    assert hook.completed[1][1] is not None


# ---- SQLite 规则存储 ----

@pytest.mark.asyncio
async def test_sqlite_rule_store_persists_and_queries(tmp_path) -> None:
    database_path = tmp_path / "vesta.db"
    store = SQLitePermissionRuleStore(database_path)
    await store.initialize()
    rule = build_safe_rule(
        tool_name="run_shell_command",
        arguments={"command": "pytest x"},
        scope=ApprovalScope.CONVERSATION,
        scope_id="conv-1",
    )
    await store.add(rule)

    reopened = SQLitePermissionRuleStore(database_path)
    await reopened.initialize()
    rules = await reopened.list(scope_ids=("conv-1",))
    assert len(rules) == 1
    assert rules[0].id == rule.id
    assert rules[0].matcher == rule.matcher
    assert rules[0].description == rule.description

    assert await reopened.remove(rule.id) is True
    assert await reopened.list(scope_ids=("conv-1",)) == ()


@pytest.mark.asyncio
async def test_sqlite_empty_scope_cannot_read_unrelated_rules(tmp_path) -> None:
    store = SQLitePermissionRuleStore(tmp_path / "vesta.db")
    await store.initialize()
    rule = build_safe_rule(
        tool_name="run_shell_command",
        arguments={"command": "pytest x"},
        scope=ApprovalScope.CONVERSATION,
        scope_id="unrelated-conversation",
    )
    await store.add(rule)

    assert await store.list(scope_ids=()) == ()
    verdict = await PermissionPolicyEngine(store).evaluate(
        tool_name="run_shell_command",
        arguments={"command": "pytest x"},
        scope_ids=(),
    )
    assert verdict.effect is PermissionEffect.ASK


@pytest.mark.asyncio
async def test_rule_store_get_and_remove_scope(tmp_path) -> None:
    store = SQLitePermissionRuleStore(tmp_path / "vesta.db")
    await store.initialize()
    first = build_safe_rule(
        tool_name="run_shell_command",
        arguments={"command": "pytest x"},
        scope=ApprovalScope.CONVERSATION,
        scope_id="conv-1",
    )
    second = build_safe_rule(
        tool_name="run_shell_command",
        arguments={"command": "pytest y"},
        scope=ApprovalScope.CONVERSATION,
        scope_id="conv-2",
    )
    await store.add(first)
    await store.add(second)

    assert await store.get(first.id) == first
    assert await store.remove_scope(ApprovalScope.CONVERSATION, "conv-1") == 1
    assert await store.get(first.id) is None
    assert await store.get(second.id) == second


@pytest.mark.asyncio
async def test_sqlite_initialize_invalidates_legacy_broad_rules(tmp_path) -> None:
    database_path = tmp_path / "vesta.db"
    store = SQLitePermissionRuleStore(database_path)
    await store.initialize()
    legacy = PermissionRule(
        id="legacy-prefix-rule",
        tool_name="run_shell_command",
        scope=ApprovalScope.CONVERSATION,
        scope_id="conv-1",
        matcher_type="command_prefix",
        matcher={"prefix": "pytest"},
        description="旧版宽泛命令规则",
    )
    await store.add(legacy)
    assert await store.get(legacy.id) is not None

    reopened = SQLitePermissionRuleStore(database_path)
    await reopened.initialize()

    assert await reopened.get(legacy.id) is None


def test_executor_rejects_mismatched_policy_and_rule_stores() -> None:
    first_store = InMemoryPermissionRuleStore()
    second_store = InMemoryPermissionRuleStore()

    with pytest.raises(ValueError, match="same store"):
        ToolExecutor(
            ToolRegistry(),
            policy_engine=PermissionPolicyEngine(first_store),
            rule_store=second_store,
        )
