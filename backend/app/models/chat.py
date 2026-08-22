"""通过已配置的模型适配器进行交互式多轮聊天。

在 backend 目录运行：

    .venv/bin/python -m app.models.chat
    .venv/bin/python -m app.models.chat --provider qwen
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from app.agent.events import (
    AgentEvent,
    AgentEventHandler,
    AgentEventType,
)
from app.application import (
    DEFAULT_SYSTEM_PROMPT,
    Application,
    title_from_content,
)
from app.checkpoint import RunCheckpoint
from app.conversation import (
    DEFAULT_DATABASE_PATH,
    Conversation,
    ConversationSource,
    SQLiteConversationStore,
    TriggerContext,
)
from app.conversation.service import ConversationService
from app.mcp import (
    DEFAULT_MCP_CONFIG_PATH,
    MCPServerState,
    MCPServerStatus,
)
from app.memory import (
    MemoryRecord,
)
from app.run import Run, RunStatus
from app.skill_learning import (
    SkillCandidate,
    SkillLearningService,
)
from app.task import (
    DEFAULT_TASKS_DIR,
)
from app.tools import (
    ApprovalScope,
    PermissionRule,
    SQLitePermissionRuleStore,
    ToolRegistry,
)
from app.tools.builtin import WebSearchTool
from app.tools.search import SearchError
from app.trace import AgentRunTrace

from .types import Message, MessageRole, ModelProvider

_COMMAND_OVERVIEW = (
    "命令：/new 新建会话，/sessions 查看会话，/use <id> 切换会话，"
    "/memories 查看长期记忆，/memory <id> 查看记忆详情，"
    "/runs 查看运行（生命周期），/run <id> 查看详情，/run cancel <id> 取消，"
    "/run recover <id> 恢复，/checkpoints 查看恢复点，/trace <id> 查看轨迹，"
    "/automations 查看自动化，/automation <id> 查看详情，"
    "/automation cancel|pause|resume <id> 管理自动化，"
    "/mcp 查看 MCP Server 与工具，"
    "/permissions 查看审批规则，"
    "/skill-candidates 查看 Skill Learning 候选，"
    "/clear 清空当前会话，/help 查看帮助，/exit 退出"
)

_HELP_TEXT = (
    "/new [标题] 新建会话\n"
    "/sessions 查看最近会话\n"
    "/use <会话ID> 切换会话\n"
    "/memories 查看活跃长期记忆及 Recall Cue\n"
    "/memory <记忆ID> 查看一条长期记忆的完整内容\n"
    "/mcp 查看 MCP Server 连接状态和已注册工具\n"
    "/runs 查看最近 Run（生命周期状态）\n"
    "/run <Run ID> 查看 Run 生命周期详情\n"
    "/run cancel <Run ID> 取消正在执行的 Run\n"
    "/run recover <Run ID> 恢复中断的 Run\n"
    "/checkpoints 查看当前会话的运行恢复点\n"
    "/automations 查看当前会话的自动化\n"
    "/automation <ID> 查看自动化详情\n"
    "/automation cancel <ID> 取消自动化\n"
    "/automation pause <ID> 暂停自动化\n"
    "/automation resume <ID> 恢复自动化\n"
    "/trace <Run ID> 查看完整事件轨迹\n"
    "/permissions 查看当前会话的审批规则\n"
    "/permission remove <规则ID> 删除一条审批规则\n"
    "/permissions clear 清除当前会话的全部审批规则\n"
    "/skill-candidates 查看待人工评审的 Skill Learning 候选\n"
    "/skill-candidate <ID> 查看候选详情\n"
    "/skill-candidate <ID> accept [scope] 接受候选：CREATE 创建 / UPDATE 更新"
    "正式 Skill（默认 project）\n"
    "/skill-candidate <ID> reject 拒绝候选\n"
    "/clear 清空当前会话\n"
    "/exit 退出聊天"
)


def _initial_history(system_prompt: str | None) -> list[Message]:
    if not system_prompt:
        return []
    return [Message(role=MessageRole.SYSTEM, content=system_prompt)]


class _CliEventHandler(AgentEventHandler):
    """把 Agent 事件打印到终端（等价原 run_stream async for 里的打印）。"""

    async def emit(self, event: AgentEvent) -> None:
        _print_agent_event(event)


async def _send_message(
    *,
    conversation_service: ConversationService,
    conversation_store: SQLiteConversationStore,
    conversation: Conversation,
    provider: ModelProvider | str,
    history: list[Message],
    content: str,
    model: str,
) -> tuple[bool, Conversation]:
    print("Vesta 正在思考...", flush=True)

    try:
        dispatch = await conversation_service.dispatch(
            conversation_id=conversation.id,
            content=content,
            trigger=TriggerContext(source=ConversationSource.MANUAL),
            event_handler=_CliEventHandler(),
        )
    except KeyboardInterrupt:
        # 用户 Ctrl+C：ConversationService 已尽力 cancel 当前 Run，
        # 回到输入循环，不退出 Vesta。
        print("\n[cancel] 已取消当前 Run。")
        return False, conversation

    result = dispatch.result
    # 会话存储已由 ConversationService 写回；同步内存 history 供 /use 等使用。
    history[:] = result.messages
    if conversation.title == "新会话":
        conversation = await conversation_store.rename(
            conversation.id,
            title_from_content(content),
        )
    answer = result.content or "<模型未返回文本>"
    print(f"\nVesta> {answer.strip()}")
    stop_reason = dispatch.run.stop_reason or result.stop_reason.value
    provider_name = provider.value if isinstance(provider, ModelProvider) else provider
    print(
        f"\n[{provider_name}/{model} · {result.steps} steps · "
        f"{result.usage.total_tokens} tokens · {stop_reason}]"
    )
    if result.tool_calls:
        tools = ", ".join(
            f"{record.tool_call.name}({'成功' if record.result.success else '失败'})"
            for record in result.tool_calls
        )
        print(f"[工具调用：{tools}]")
    if dispatch.run.status.value == "cancelled":
        print("[Run 已被取消]")
    return result.ok, conversation


def _print_agent_event(event: AgentEvent) -> None:
    """把 Runtime 事件转换为简洁的终端进度信息。"""

    event_time = event.event_time.astimezone().strftime("%H:%M:%S")
    prefix = f"[{event_time}]"
    if event.type is AgentEventType.AGENT_STARTED:
        print(f"{prefix} Agent 开始执行")
    elif event.type is AgentEventType.MODEL_STARTED:
        print(f"{prefix} 第 {event.step} 步：正在请求模型")
    elif event.type is AgentEventType.MODEL_COMPLETED:
        tool_count = len(event.message.tool_calls) if event.message else 0
        if tool_count:
            print(f"{prefix} 模型请求调用 {tool_count} 个工具")
        else:
            print(f"{prefix} 模型已返回回复")
    elif event.type is AgentEventType.TOOL_STARTED and event.tool_call:
        print(f"{prefix} 开始执行工具：{event.tool_call.name}")
    elif event.type is AgentEventType.TOOL_COMPLETED and event.tool_result:
        status = "成功" if event.tool_result.success else "失败"
        print(
            f"{prefix} 工具 {event.tool_result.tool_name} {status} "
            f"({event.tool_result.duration_ms:.1f}ms)"
        )
    elif event.type is AgentEventType.TOOL_APPROVAL_REQUIRED and event.tool_call:
        print(f"{prefix} 工具等待人工审批：{event.tool_call.name}")
    elif event.type is AgentEventType.TOOL_APPROVAL_COMPLETED and event.tool_call:
        decision = (
            event.approval_decision.value
            if event.approval_decision is not None
            else "unknown"
        )
        rule_text = (
            f" · 规则：{event.rule_description}"
            if event.rule_description is not None
            else ""
        )
        print(
            f"{prefix} 工具权限检查完成：{event.tool_call.name} · {decision}{rule_text}"
        )
    elif event.type is AgentEventType.MEMORY_REFLECTION_STARTED:
        print(f"{prefix} 正在整理本轮长期记忆")
    elif event.type is AgentEventType.MEMORY_REFLECTION_COMPLETED:
        action = event.reflection_action or "none"
        suffix = (
            f" · {event.reflection_memory_id}"
            if event.reflection_memory_id is not None
            else ""
        )
        print(f"{prefix} 长期记忆整理完成：{action}{suffix}")
    elif event.type is AgentEventType.MEMORY_REFLECTION_FAILED:
        print(f"{prefix} 长期记忆整理失败，已跳过")
    elif event.type is AgentEventType.MEMORY_REFLECTION_SKIPPED:
        reason = event.reflection_skip_reason or "policy"
        print(f"{prefix} 长期记忆整理已跳过：{reason}")
    elif event.type is AgentEventType.MEMORY_MAINTENANCE_STARTED:
        print(f"{prefix} 长期记忆容量不足，正在选择可归档候选")
    elif event.type is AgentEventType.MEMORY_MAINTENANCE_COMPLETED:
        action = event.maintenance_action or "unknown"
        suffix = (
            f" · {event.maintenance_memory_id}"
            if event.maintenance_memory_id is not None
            else ""
        )
        print(f"{prefix} 长期记忆容量维护完成：{action}{suffix}")
    elif event.type is AgentEventType.MEMORY_MAINTENANCE_FAILED:
        print(f"{prefix} 长期记忆容量维护失败，未执行归档")
    elif event.type is AgentEventType.AGENT_COMPLETED:
        print(f"{prefix} Agent 执行完成")
    elif event.type is AgentEventType.AGENT_FAILED:
        reason = event.stop_reason.value if event.stop_reason else "unknown"
        print(f"{prefix} Agent 执行停止：{reason}")


async def _load_or_create_conversation(
    store: SQLiteConversationStore,
    *,
    identifier: str | None,
    force_new: bool,
    system_prompt: str | None,
) -> tuple[Conversation, list[Message], bool]:
    """加载指定或最近会话；不存在时创建新会话。"""

    if force_new:
        conversation = await store.create(messages=_initial_history(system_prompt))
        return conversation, list(await store.load_messages(conversation.id)), False

    if identifier:
        conversation = await store.resolve(identifier)
        if conversation is None:
            raise ValueError(f"找不到会话：{identifier}")
    else:
        conversation = await store.latest()

    if conversation is None:
        conversation = await store.create(messages=_initial_history(system_prompt))
        return conversation, list(await store.load_messages(conversation.id)), False

    history = list(await store.load_messages(conversation.id))
    return conversation, history, True


def _print_conversations(
    conversations: tuple[Conversation, ...],
    current_id: str,
) -> None:
    if not conversations:
        print("暂无会话。")
        return
    for conversation in conversations:
        marker = "*" if conversation.id == current_id else " "
        print(
            f"{marker} {conversation.id[:8]}  "
            f"{conversation.title}  ({conversation.message_count} 条消息)"
        )


def _print_runs(runs: tuple[AgentRunTrace, ...], current_conversation_id: str) -> None:
    """显示最近的 Agent Run 摘要。"""

    if not runs:
        print("暂无运行记录。")
        return
    for run in runs:
        marker = "*" if run.conversation_id == current_conversation_id else " "
        started_at = run.started_at.astimezone().strftime("%m-%d %H:%M:%S")
        reason = run.stop_reason.value if run.stop_reason else "-"
        print(
            f"{marker} {run.run_id[:8]}  {started_at}  {run.status.value:<9} "
            f"steps={run.steps} tokens={run.total_tokens} reason={reason}"
        )


def _print_run_lifecycle(runs: tuple[Run, ...], current_conversation_id: str) -> None:
    """显示 Run 生命周期记录（含 CANCELLED / INTERRUPTED 等完整状态）。"""

    if not runs:
        print("暂无 Run 记录。")
        return
    for run in runs:
        marker = "*" if run.conversation_id == current_conversation_id else " "
        created_at = run.created_at.astimezone().strftime("%m-%d %H:%M:%S")
        stop_reason = run.stop_reason or "-"
        print(
            f"{marker} {run.id[:8]}  {created_at}  "
            f"{run.status.value:<11} reason={stop_reason}"
        )


def _print_run_detail(run: Run) -> None:
    """显示单个 Run 的生命周期详情。"""

    if run is None:
        print("找不到 Run。")
        return
    started = (
        run.started_at.astimezone().strftime("%m-%d %H:%M:%S")
        if run.started_at is not None
        else "-"
    )
    completed = (
        run.completed_at.astimezone().strftime("%m-%d %H:%M:%S")
        if run.completed_at is not None
        else "-"
    )
    print(f"Run {run.id}")
    print(f"  conversation_id: {run.conversation_id or '-'}")
    print(f"  status: {run.status.value}")
    print(f"  created_at: {run.created_at.astimezone().strftime('%m-%d %H:%M:%S')}")
    print(f"  started_at: {started}")
    print(f"  completed_at: {completed}")
    print(f"  stop_reason: {run.stop_reason or '-'}")
    if run.error:
        print(f"  error: {run.error}")
    if run.recovered_from_run_id:
        print(f"  recovered_from: {run.recovered_from_run_id[:8]}")
    print(f"  user_message: {(run.user_message or '')[:120]}")


def _print_automations(automations: tuple[object, ...]) -> None:
    """显示自动化列表。"""

    if not automations:
        print("暂无 Automation。")
        return
    for item in automations:
        next_run = (
            item.next_run_at.astimezone().strftime("%m-%d %H:%M")
            if item.next_run_at is not None
            else "-"
        )
        print(
            f"{item.id[:8]}  {item.title[:24]:<24}  "
            f"{item.status.value:<9} next={next_run}"
        )


def _print_automation(automation: object) -> None:
    """显示单个自动化详情。"""

    if automation is None:
        print("找不到 Automation。")
        return
    schedule = automation.schedule
    print(f"Automation {automation.id}")
    print(f"  title: {automation.title}")
    print(f"  status: {automation.status.value}")
    print(f"  kind: {schedule.kind.value}")
    if schedule.kind.value == "once":
        print(f"  run_at: {schedule.run_at}")
    elif schedule.kind.value == "interval":
        print(f"  interval_seconds: {schedule.interval_seconds}")
    else:
        print(f"  cron_expr: {schedule.cron_expr}")
    print(f"  timezone: {schedule.timezone}")
    print(f"  conversation_id: {automation.conversation_id or '-'}")
    next_run = (
        automation.next_run_at.astimezone().strftime("%m-%d %H:%M:%S")
        if automation.next_run_at is not None
        else "-"
    )
    print(f"  next_run_at: {next_run}")
    print(f"  last_run_id: {automation.last_run_id or '-'}")
    print(f"  prompt: {(automation.prompt or '')[:160]}")


def _print_checkpoints(checkpoints: tuple[RunCheckpoint, ...]) -> None:
    """显示当前会话最近的恢复边界。"""

    if not checkpoints:
        print("当前会话暂无 Checkpoint。")
        return
    for checkpoint in checkpoints:
        updated_at = checkpoint.updated_at.astimezone().strftime("%m-%d %H:%M:%S")
        pending = len(checkpoint.pending_tool_calls)
        print(
            f"{checkpoint.run_id[:8]}  {updated_at}  "
            f"{checkpoint.status.value:<11} phase={checkpoint.phase.value} "
            f"step={checkpoint.step} pending_tools={pending}"
        )


def _print_mcp_statuses(statuses: tuple[MCPServerStatus, ...]) -> None:
    """显示 MCP Server 状态和已注册工具。"""

    if not statuses:
        print("尚未配置 MCP Server。")
        return
    for status in statuses:
        marker = "✓" if status.state is MCPServerState.RUNNING else "-"
        print(f"{marker} {status.name} · {status.state.value}")
        if status.error:
            print(f"  错误：{status.error}")
        for tool_name in status.tool_names:
            print(f"  - {tool_name}")


def _print_recovered_runs(runs: tuple[Run, ...]) -> None:
    """展示启动 reconciliation / 会话切换发现的未正常结束 Run（只读展示）。"""

    for run in runs:
        if run.status is RunStatus.INTERRUPTED:
            print(
                f"检测到中断 Run：{run.id[:8]} · 可恢复 "
                f"（/run recover {run.id[:8]} 继续执行）"
            )
        else:
            print(
                f"Run 状态修正：{run.id[:8]} RUNNING → {run.status.value}"
                + (f" · {run.error}" if run.error else "")
            )


def _print_trace(events: tuple[AgentEvent, ...]) -> None:
    """显示一次 Run 的完整事件时间线。"""

    for event in events:
        event_time = event.event_time.astimezone().strftime("%H:%M:%S.%f")[:-3]
        details: list[str] = []
        if event.step is not None:
            details.append(f"step={event.step}")
        if event.tool_call is not None:
            details.append(f"tool={event.tool_call.name}")
        if event.approval_decision is not None:
            details.append(f"decision={event.approval_decision.value}")
        if event.rule_id is not None:
            details.append(f"rule={event.rule_id[:8]}")
        if event.tool_result is not None:
            details.append(
                f"success={'true' if event.tool_result.success else 'false'}"
            )
        if event.type is AgentEventType.MODEL_STARTED:
            if event.prepared_input_tokens is not None:
                details.append(f"input≈{event.prepared_input_tokens}")
            if event.tool_schema_tokens is not None:
                details.append(f"schemas≈{event.tool_schema_tokens}")
            if (
                event.tool_result_tokens_before is not None
                and event.tool_result_tokens_after is not None
            ):
                details.append(
                    "tool_results≈"
                    f"{event.tool_result_tokens_before}→"
                    f"{event.tool_result_tokens_after}"
                )
            if event.compaction_stage not in (None, "none"):
                details.append(f"context={event.compaction_stage}")
        detail_text = f"  {' '.join(details)}" if details else ""
        print(f"{event.sequence:03d}  {event_time}  {event.type.value}{detail_text}")


def _print_skill_candidates(candidates: tuple[SkillCandidate, ...]) -> None:
    """输出 Skill Learning 候选列表。"""

    if not candidates:
        print("没有 Skill Learning 候选。")
        return
    for candidate in candidates:
        print(
            f"{candidate.id}  [{candidate.status.value}] "
            f"{candidate.action.value.upper()} {candidate.proposed_name} "
            f"· 来源 {len(candidate.source_task_ids)} Task"
        )


async def _maybe_run_skill_learning(
    skill_learning: SkillLearningService,
) -> None:
    """交互后尝试触发一次 Skill Learning（只有累计满 batch 才会调用模型）。"""

    try:
        outcome = await skill_learning.maybe_run_mining()
    except Exception as exc:
        print(f"Skill Learning 失败：{type(exc).__name__}: {exc}")
        return
    if not outcome.triggered:
        return
    print("Skill Learning:")
    print(f"  tasks scanned: {outcome.scanned_task_count}")
    print(f"  clusters: {outcome.cluster_count}")
    print(f"  candidates: {outcome.candidate_count}")
    print(f"  model calls: {outcome.pattern_mining_calls + outcome.distillation_calls}")
    print(f"  input tokens: {outcome.input_tokens}")
    print(f"  output tokens: {outcome.output_tokens}")
    print(f"  total tokens: {outcome.total_tokens}")
    print(f"  latency: {outcome.total_duration_ms / 1000:.1f}s")
    if outcome.error:
        print(f"  error: {outcome.error}")
    if outcome.candidate_count:
        print("  运行 /skill-candidates 查看待评审候选")


def _mark_deferred_tools(
    registry: ToolRegistry,
    names: frozenset[str],
) -> None:
    """把不常用工具标记为按需暴露。

    默认只向模型暴露核心工具；被标记的工具不进入请求的 schema，
    模型需要时通过 ``tool_search`` 搜索并激活后使用。
    """

    for name in names:
        tool = registry.unregister(name)
        registry.register(tool, deferred=True)


def _print_permission_rules(rules: tuple[PermissionRule, ...]) -> None:
    """显示当前会话记住的工具审批规则。"""

    if not rules:
        print("当前会话没有已记住的审批规则。")
        return
    for rule in rules:
        created_at = rule.created_at.astimezone().strftime("%m-%d %H:%M:%S")
        print(f"{rule.id[:8]}  {created_at}  {rule.tool_name}  {rule.description}")


async def _remove_permission_rule(
    store: SQLitePermissionRuleStore,
    conversation_id: str,
    identifier: str,
) -> bool:
    """按当前会话中的完整 ID 或唯一前缀删除规则。"""

    normalized = identifier.strip()
    if not normalized:
        return False
    rules = await store.list(scope_ids=(conversation_id,))
    matched = [rule for rule in rules if rule.id.startswith(normalized)]
    if len(matched) > 1:
        raise ValueError(f"规则 ID 前缀不唯一：{identifier}")
    if not matched:
        return False
    return await store.remove(matched[0].id)


def _print_memories(memories: Sequence[MemoryRecord]) -> None:
    """输出适合终端快速浏览的长期记忆列表（Recall Cue）。"""

    if not memories:
        print("没有长期记忆。")
        return
    for memory in memories:
        cue = " ".join(memory.summary.split())
        print(f"{memory.id}  [{memory.status.value}]  {memory.title}")
        print(f"  Cue: {cue}")


def _print_memory(memory: MemoryRecord) -> None:
    """输出单条长期记忆的完整内容。"""

    print(
        f"ID: {memory.id}\n"
        f"状态: {memory.status.value}\n"
        f"标题: {memory.title}\n"
        f"访问次数: {memory.access_count}\n"
        f"创建时间: {memory.created_at.astimezone().isoformat()}\n"
        f"内容:\n{memory.render_full()}"
    )


async def _run(args: argparse.Namespace) -> int:
    app = Application(
        provider=args.provider,
        model=args.model,
        system_prompt=args.system,
        database=args.database,
        tasks_dir=args.tasks_dir,
        mcp_config=args.mcp_config,
        max_steps=args.max_steps,
        max_tool_rounds=args.max_tool_rounds,
        max_output_tokens=args.max_output_tokens,
    )
    provider = app.provider
    model = app.model
    print(f"Vesta Chat · provider={provider} · model={model}")

    try:
        await app.start()
    except SearchError as exc:
        print(f"搜索配置错误：{exc}", file=sys.stderr)
        return 2
    if app.mcp_error is not None:
        print(f"MCP 配置错误：{app.mcp_error}", file=sys.stderr)
        return 2

    # 与 CLI 循环体保持一致的局部变量名（值统一来自 Application）。
    conversation_store = app.conversation_store
    summary_store = app.summary_store
    trace_store = app.trace_store
    checkpoint_store = app.checkpoint_store
    rule_store = app.rule_store
    run_manager = app.run_manager
    conversation_service = app.conversation_service
    automation_scheduler = app.automation_scheduler
    memory_manager = app.memory_manager
    skill_learning = app.skill_learning
    mcp_manager = app.mcp_manager
    tool_registry = app.tool_registry
    try:
        conversation, history, resumed = await _load_or_create_conversation(
            conversation_store,
            identifier=args.conversation,
            force_new=args.new_conversation,
            system_prompt=args.system,
        )
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    action = "已恢复" if resumed else "已创建"
    print(
        f"{action}会话：{conversation.id[:8]} · "
        f"{conversation.title} · {conversation.message_count} 条消息"
    )
    search_tool = tool_registry.get("web_search")
    if isinstance(search_tool, WebSearchTool):
        if search_tool.provider_name == "tavily":
            print("联网搜索：Tavily（服务异常时自动回退 DuckDuckGo）")
        else:
            print("联网搜索：DuckDuckGo（配置 TAVILY_API_KEY 可启用 Tavily）")
    if skill_learning.settings.skill_learning_enabled:
        print(
            "Skill Learning：每 "
            f"{skill_learning.settings.skill_learning_batch_size} 个 Completed "
            "Task 触发一次 Pattern Mining（候选需人工评审）"
        )
    reflection_status = "启用" if app.memory_reflection_enabled else "关闭"
    reflection_model = app.memory_reflector.model_hint or "未解析"
    reflection_provider = app.memory_reflector.provider_hint or "未解析"
    print(
        "长期记忆：Sparse Memory（在线 Recall + 显式 Core + Post-Run Reflection）"
    )
    print(f"记忆反思：{reflection_status} · {reflection_provider}/{reflection_model}")
    maintenance_status = "启用" if app.memory_maintenance_enabled else "关闭"
    maintenance_model = app.memory_maintenance_reflector.model_hint or "未解析"
    maintenance_provider = app.memory_maintenance_reflector.provider_hint or "未解析"
    print(
        f"容量维护：{maintenance_status} · "
        f"{maintenance_provider}/{maintenance_model}"
    )
    connected_mcp = sum(
        status.state is MCPServerState.RUNNING for status in app.mcp_statuses
    )
    failed_mcp = sum(
        status.state is MCPServerState.FAILED for status in app.mcp_statuses
    )
    if app.mcp_statuses:
        print(
            f"MCP：{connected_mcp} 个 Server 已连接，"
            f"{failed_mcp} 个启动失败"
        )
    if app.reconciled_runs:
        _print_recovered_runs(app.reconciled_runs)
    try:
        if args.message is not None:
            success, conversation = await _send_message(
                conversation_service=conversation_service,
                conversation_store=conversation_store,
                conversation=conversation,
                provider=provider,
                history=history,
                content=args.message,
                model=model,
            )
            await _maybe_run_skill_learning(skill_learning)
            return 0 if success else 1

        print(_COMMAND_OVERVIEW)
        while True:
            try:
                # 用 to_thread 避免同步 input() 阻塞 asyncio 事件循环，
                # 确保用户停在输入框时 Scheduler 仍能按时触发 Automation。
                content = (await asyncio.to_thread(input, "\n你> ")).strip()
            except (EOFError, KeyboardInterrupt):
                print("\n聊天已结束。")
                return 0

            if not content:
                continue
            if content in {"/exit", "/quit"}:
                print("聊天已结束。")
                return 0
            if content == "/new" or content.startswith("/new "):
                title = content.removeprefix("/new").strip() or "新会话"
                conversation = await conversation_store.create(
                    title=title,
                    messages=_initial_history(args.system),
                )
                history = list(await conversation_store.load_messages(conversation.id))
                print(f"已创建会话：{conversation.id[:8]} · {conversation.title}")
                continue
            if content == "/sessions":
                _print_conversations(
                    await conversation_store.list(),
                    conversation.id,
                )
                continue
            if content == "/memories" or content.startswith("/memories "):
                _print_memories(await memory_manager.list())
                continue
            if content == "/mcp":
                _print_mcp_statuses(mcp_manager.statuses())
                continue
            if content == "/memory" or content.startswith("/memory "):
                identifier = content.removeprefix("/memory").strip()
                if not identifier:
                    print("用法：/memory <记忆ID>")
                    continue
                memory = await memory_manager.read(identifier)
                if memory is None:
                    print(f"找不到记忆：{identifier}")
                    continue
                _print_memory(memory)
                continue
            if content == "/permissions":
                rules = await rule_store.list(scope_ids=(conversation.id,))
                _print_permission_rules(rules)
                continue
            if content == "/permissions clear":
                removed = await rule_store.remove_scope(
                    ApprovalScope.CONVERSATION,
                    conversation.id,
                )
                print(f"已清除当前会话的 {removed} 条审批规则。")
                continue
            if content == "/permission remove" or content.startswith(
                "/permission remove "
            ):
                identifier = content.removeprefix("/permission remove").strip()
                if not identifier:
                    print("用法：/permission remove <规则ID>")
                    continue
                try:
                    removed = await _remove_permission_rule(
                        rule_store,
                        conversation.id,
                        identifier,
                    )
                except ValueError as exc:
                    print(exc)
                    continue
                if removed:
                    print(f"已删除审批规则：{identifier}")
                else:
                    print(f"当前会话找不到审批规则：{identifier}")
                continue
            if content == "/runs":
                _print_run_lifecycle(
                    await run_manager.list_runs(),
                    conversation.id,
                )
                continue
            if content == "/run" or content.startswith("/run "):
                parts = content.split()
                if len(parts) == 1:
                    _print_run_lifecycle(
                        await run_manager.list_runs(
                            conversation_id=conversation.id,
                        ),
                        conversation.id,
                    )
                    continue
                action_word = parts[1].lower()
                if action_word in ("cancel", "recover"):
                    if len(parts) < 3:
                        print(f"用法：/run {action_word} <Run ID>")
                        continue
                    identifier = parts[2]
                    run = await run_manager.get_run(identifier)
                    if run is None:
                        print(f"找不到 Run：{identifier}")
                        continue
                    if action_word == "cancel":
                        try:
                            updated = await run_manager.cancel(run.id)
                        except (KeyError, ValueError) as exc:
                            print(exc)
                            continue
                        print(
                            f"Run {updated.id[:8]} 已取消：{updated.status.value}"
                        )
                    else:
                        # recover 完整链路由 ConversationService 统一收口。
                        try:
                            dispatch = await conversation_service.recover(
                                run.id,
                                event_handler=_CliEventHandler(),
                            )
                        except (KeyError, ValueError) as exc:
                            print(exc)
                            continue
                        recovered_run = dispatch.run
                        if dispatch.result is not None:
                            history[:] = dispatch.result.messages
                        print(
                            f"Run {recovered_run.id[:8]} 恢复自 "
                            f"{run.id[:8]} · {recovered_run.status.value}"
                        )
                    continue
                # /run <id> 查看详情
                run = await run_manager.get_run(parts[1])
                if run is None:
                    print(f"找不到 Run：{parts[1]}")
                    continue
                _print_run_detail(run)
                continue
            if content == "/checkpoints":
                _print_checkpoints(
                    await checkpoint_store.list(
                        conversation_id=conversation.id,
                    )
                )
                continue
            if content == "/trace" or content.startswith("/trace "):
                identifier = content.removeprefix("/trace").strip()
                if not identifier:
                    print("用法：/trace <Run ID>")
                    continue
                try:
                    run = await trace_store.resolve(identifier)
                except ValueError as exc:
                    print(exc)
                    continue
                if run is None:
                    print(f"找不到 Run：{identifier}")
                    continue
                print(
                    f"Run {run.run_id} · {run.status.value} · {run.event_count} 个事件"
                )
                _print_trace(await trace_store.load_events(run.run_id))
                continue
            if content == "/automations":
                _print_automations(
                    await automation_scheduler.list(
                        conversation_id=conversation.id,
                    )
                )
                continue
            if content == "/automation" or content.startswith("/automation "):
                parts = content.split()
                if len(parts) < 2:
                    _print_automations(
                        await automation_scheduler.list(
                            conversation_id=conversation.id,
                        )
                    )
                    continue
                action_word = parts[1].lower()
                if action_word in ("cancel", "pause", "resume"):
                    if len(parts) < 3:
                        print(f"用法：/automation {action_word} <ID>")
                        continue
                    identifier = parts[2]
                    try:
                        automation = await automation_scheduler.resolve(
                            identifier
                        )
                    except ValueError as exc:
                        print(exc)
                        continue
                    if automation is None:
                        print(f"找不到 Automation：{identifier}")
                        continue
                    try:
                        if action_word == "cancel":
                            updated = await automation_scheduler.cancel(
                                automation.id
                            )
                        elif action_word == "pause":
                            updated = await automation_scheduler.pause(
                                automation.id
                            )
                        else:
                            updated = await automation_scheduler.resume(
                                automation.id
                            )
                    except ValueError as exc:
                        print(exc)
                        continue
                    print(
                        f"Automation {updated.id[:8]} "
                        f"{action_word} → {updated.status.value}"
                    )
                    continue
                automation = await automation_scheduler.get(parts[1])
                if automation is None:
                    print(f"找不到 Automation：{parts[1]}")
                    continue
                _print_automation(automation)
                continue
            if content == "/use" or content.startswith("/use "):
                identifier = content.removeprefix("/use").strip()
                if not identifier:
                    print("用法：/use <会话ID>")
                    continue
                try:
                    selected = await conversation_store.resolve(identifier)
                except ValueError as exc:
                    print(exc)
                    continue
                if selected is None:
                    print(f"找不到会话：{identifier}")
                    continue
                conversation = selected
                history = list(await conversation_store.load_messages(conversation.id))
                # 只读展示该会话中断的 Run，生命周期修改统一走 RunManager。
                _print_recovered_runs(
                    await run_manager.list_runs(
                        conversation_id=conversation.id,
                        status=RunStatus.INTERRUPTED,
                    )
                )
                print(
                    f"已切换会话：{conversation.id[:8]} · "
                    f"{conversation.title} · {conversation.message_count} 条消息"
                )
                continue
            if content == "/skill-candidates":
                _print_skill_candidates(
                    await skill_learning.list_candidates()
                )
                continue
            if content == "/skill-candidate" or content.startswith(
                "/skill-candidate "
            ):
                parts = content.split()
                if len(parts) < 2:
                    print("用法：/skill-candidate <ID> [accept [scope] | reject]")
                    continue
                candidate = await skill_learning.get_candidate(parts[1])
                if candidate is None:
                    print(f"找不到候选：{parts[1]}")
                    continue
                if len(parts) == 2:
                    print(skill_learning.render_candidate_details(candidate))
                    continue
                action_word = parts[2].lower()
                if action_word == "accept":
                    scope = parts[3] if len(parts) > 3 else None
                    try:
                        updated, target = await skill_learning.accept(
                            candidate.id,
                            scope=scope,
                        )
                    except (KeyError, ValueError) as exc:
                        print(exc)
                        continue
                    if updated.action.value == "update":
                        print(
                            "已接受 UPDATE 候选，已应用更新到正式 Skill "
                            f"{updated.existing_skill_name}。"
                        )
                        print(f"Updated Skill: {updated.existing_skill_name}")
                    else:
                        print(
                            "已接受 CREATE 候选，已创建正式 Skill "
                            f"{updated.proposed_name}。"
                        )
                        print(f"Created Skill: {updated.proposed_name}")
                    if target is not None:
                        print(f"Path: {target}")
                    print(f"Status: {updated.status.value.upper()}")
                    continue
                if action_word == "reject":
                    try:
                        await skill_learning.reject(candidate.id)
                    except (KeyError, ValueError) as exc:
                        print(exc)
                        continue
                    print(f"已拒绝候选 {candidate.proposed_name}。")
                    continue
                print(f"未知操作：{action_word}（支持 accept / reject）")
                continue
            if content == "/clear":
                history = _initial_history(args.system)
                conversation = await conversation_store.replace_messages(
                    conversation.id,
                    history,
                )
                await summary_store.delete(conversation.id)
                print("上下文已清空。")
                continue
            if content == "/help":
                print(_HELP_TEXT)
                continue

            _, conversation = await _send_message(
                conversation_service=conversation_service,
                conversation_store=conversation_store,
                conversation=conversation,
                provider=provider,
                history=history,
                content=content,
                model=model,
            )
            await _maybe_run_skill_learning(skill_learning)
    finally:
        await app.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Chat with a configured Vesta model provider."
    )
    parser.add_argument(
        "--provider",
        choices=[provider.value for provider in ModelProvider],
        help="Provider to use; auto-selects the default or only configured provider.",
    )
    parser.add_argument("--model", help="Override the configured model name.")
    parser.add_argument(
        "--message",
        help="Send one message and exit instead of opening interactive chat.",
    )
    parser.add_argument(
        "--system",
        default=DEFAULT_SYSTEM_PROMPT,
        help="System prompt for this conversation.",
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=None,
        help=(
            "Maximum output tokens for each reply; defaults to the configured "
            "provider value."
        ),
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=10,
        help="Maximum model/tool loop steps for each message.",
    )
    parser.add_argument(
        "--max-tool-rounds",
        type=int,
        default=15,
        help="Maximum tool-calling rounds before forcing a final answer.",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help="SQLite conversation database path.",
    )
    parser.add_argument(
        "--tasks-dir",
        type=Path,
        default=DEFAULT_TASKS_DIR,
        help="Directory containing persistent task JSON files.",
    )
    parser.add_argument(
        "--mcp-config",
        type=Path,
        default=DEFAULT_MCP_CONFIG_PATH,
        help="Path to the MCP Server JSON configuration file.",
    )
    parser.add_argument(
        "--conversation",
        help="Resume a conversation by full ID or unique ID prefix.",
    )
    parser.add_argument(
        "--new-conversation",
        action="store_true",
        help="Start a new conversation instead of restoring the latest one.",
    )
    args = parser.parse_args()
    if args.max_output_tokens is not None and args.max_output_tokens <= 0:
        parser.error("--max-output-tokens must be greater than zero")
    if args.max_steps <= 0:
        parser.error("--max-steps must be greater than zero")
    if args.max_tool_rounds <= 0:
        parser.error("--max-tool-rounds must be greater than zero")
    if args.conversation and args.new_conversation:
        parser.error("--conversation and --new-conversation cannot be used together")
    return args


def main() -> None:
    raise SystemExit(asyncio.run(_run(_parse_args())))


if __name__ == "__main__":
    main()
