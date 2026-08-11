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
from datetime import datetime
from pathlib import Path

from app.agent.events import AgentEvent, AgentEventType
from app.agent.result import AgentResult
from app.agent.runtime import AgentRuntime
from app.checkpoint import RunCheckpoint, SQLiteCheckpointStore
from app.context import (
    ContextManager,
    ContextSettings,
    ConversationReducer,
    ModelContextSummarizer,
    SQLiteConversationSummaryStore,
)
from app.conversation import (
    DEFAULT_DATABASE_PATH,
    Conversation,
    SQLiteConversationStore,
)
from app.memory import (
    MemoryManager,
    MemoryRecord,
    register_memory_tools,
)
from app.task import (
    DEFAULT_TASKS_DIR,
    FileTaskStore,
    TaskContextProvider,
    register_task_tools,
)
from app.tools import (
    ApprovalScope,
    ConsoleApprovalGate,
    PermissionPolicyEngine,
    PermissionRule,
    SQLitePermissionRuleStore,
    build_builtin_tool_registry,
    describe_safe_rule,
)
from app.tools.builtin import WebSearchTool
from app.tools.search import SearchError
from app.trace import (
    AgentRunTrace,
    SQLiteTraceEventHandler,
    SQLiteTraceStore,
)

from .config import ModelSettings
from .registry import ModelAdapterRegistry
from .types import Message, MessageRole, ModelProvider


def _select_provider(
    settings: ModelSettings,
    requested: str | None,
) -> ModelProvider:
    configured = settings.configured_providers()

    if requested is not None:
        provider = ModelProvider(requested)
        if provider not in configured:
            raise ValueError(
                f"Provider '{provider.value}' is not configured in backend/.env."
            )
        return provider

    if settings.model_default_provider in configured:
        return settings.model_default_provider
    if len(configured) == 1:
        return configured[0]
    if not configured:
        raise ValueError("No model provider is configured in backend/.env.")
    names = ", ".join(provider.value for provider in configured)
    raise ValueError(
        f"Multiple providers are configured ({names}); use --provider to select one."
    )


def _initial_history(system_prompt: str | None) -> list[Message]:
    if not system_prompt:
        return []
    return [Message(role=MessageRole.SYSTEM, content=system_prompt)]


async def _send_message(
    *,
    runtime: AgentRuntime,
    conversation_store: SQLiteConversationStore,
    trace_handler: SQLiteTraceEventHandler,
    conversation: Conversation,
    provider: ModelProvider,
    history: list[Message],
    content: str,
    model: str,
    summary_store: SQLiteConversationSummaryStore | None = None,
) -> tuple[bool, Conversation]:
    print("OneAgent 正在思考...", flush=True)

    result: AgentResult | None = None
    summary_state = (
        await summary_store.load(conversation.id) if summary_store is not None else None
    )
    async for event in runtime.run_stream(
        content,
        history=history,
        conversation_id=conversation.id,
        event_handler=trace_handler,
        summary_state=summary_state,
    ):
        _print_agent_event(event)
        if event.result is not None:
            result = event.result

    if result is None:
        raise RuntimeError("Agent 事件流结束时缺少最终结果")
    # 会话存储保存完整原始历史；模型请求压缩由 ContextManager 单独负责。
    history[:] = result.messages
    conversation = await conversation_store.replace_messages(
        conversation.id,
        history,
    )
    if summary_store is not None and result.summary_state is not None:
        await summary_store.save(conversation.id, result.summary_state)
    if conversation.title == "新会话":
        conversation = await conversation_store.rename(
            conversation.id,
            _title_from_content(content),
        )
    answer = result.content or "<模型未返回文本>"
    print(f"\nOneAgent> {answer.strip()}")
    print(
        f"\n[{provider.value}/{model} · {result.steps} steps · "
        f"{result.usage.total_tokens} tokens · {result.stop_reason.value}]"
    )
    if result.tool_calls:
        tools = ", ".join(
            f"{record.tool_call.name}({'成功' if record.result.success else '失败'})"
            for record in result.tool_calls
        )
        print(f"[工具调用：{tools}]")
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


def _title_from_content(content: str) -> str:
    title = " ".join(content.split()).strip()
    return title[:40] or "新会话"


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


def _print_recovered_checkpoints(
    checkpoints: tuple[RunCheckpoint, ...],
) -> None:
    """提示启动时发现的未正常结束 Run。"""

    for checkpoint in checkpoints:
        print(
            "检测到中断 Run："
            f"{checkpoint.run_id[:8]} · phase={checkpoint.phase.value} · "
            f"step={checkpoint.step} · "
            f"待核对工具={len(checkpoint.pending_tool_calls)}。"
            "下次对话会先把恢复证据提供给模型。"
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
        detail_text = f"  {' '.join(details)}" if details else ""
        print(f"{event.sequence:03d}  {event_time}  {event.type.value}{detail_text}")


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
    settings = ModelSettings()
    try:
        provider = _select_provider(settings, args.provider)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    config = settings.provider_config(provider)
    model = args.model or config.model
    print(f"OneAgent Chat · provider={provider.value} · model={model}")

    conversation_store = SQLiteConversationStore(args.database)
    await conversation_store.initialize()
    summary_store = SQLiteConversationSummaryStore(args.database)
    await summary_store.initialize()
    trace_store = SQLiteTraceStore(args.database)
    await trace_store.initialize()
    trace_handler = SQLiteTraceEventHandler(trace_store)
    checkpoint_store = SQLiteCheckpointStore(args.database)
    await checkpoint_store.initialize()
    rule_store = SQLitePermissionRuleStore(args.database)
    await rule_store.initialize()
    policy_engine = PermissionPolicyEngine(rule_store)
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
    _print_recovered_checkpoints(
        await checkpoint_store.recover_running(
            conversation_id=conversation.id,
        )
    )
    try:
        tool_registry = build_builtin_tool_registry()
    except SearchError as exc:
        print(f"搜索配置错误：{exc}", file=sys.stderr)
        return 2
    task_store = FileTaskStore(args.tasks_dir)
    await task_store.initialize()
    register_task_tools(tool_registry, task_store)
    search_tool = tool_registry.get("web_search")
    if isinstance(search_tool, WebSearchTool):
        if search_tool.provider_name == "tavily":
            print("联网搜索：Tavily（服务异常时自动回退 DuckDuckGo）")
        else:
            print("联网搜索：DuckDuckGo（配置 TAVILY_API_KEY 可启用 Tavily）")

    registry = ModelAdapterRegistry(settings)
    memory_manager = MemoryManager()
    await memory_manager.initialize()
    register_memory_tools(tool_registry, memory_manager)
    print("长期记忆：Sparse Model-Directed Memory（CORE + INDEX + 语义工具）")
    context_settings = ContextSettings()
    context_summarizer = ModelContextSummarizer(
        registry,
        provider=provider,
        model=args.model,
        max_output_tokens=context_settings.context_summary_max_output_tokens,
    )
    runtime = AgentRuntime(
        registry,
        tool_registry,
        provider=provider,
        model=args.model,
        max_steps=args.max_steps,
        max_tool_rounds=args.max_tool_rounds,
        max_output_tokens=args.max_output_tokens,
        approval_gate=ConsoleApprovalGate(
            rule_label_factory=describe_safe_rule,
        ),
        policy_engine=policy_engine,
        rule_store=rule_store,
        context_manager=ContextManager(
            context_settings=context_settings,
            conversation_reducer=ConversationReducer(
                context_summarizer,
                keep_recent_conversation_blocks=(
                    context_settings.context_keep_recent_conversation_blocks
                ),
                keep_recent_tool_rounds=(
                    context_settings.context_keep_recent_tool_rounds
                ),
            ),
        ),
        task_context_provider=TaskContextProvider(task_store),
        checkpoint_store=checkpoint_store,
        memory_manager=memory_manager,
    )
    try:
        if args.message is not None:
            success, conversation = await _send_message(
                runtime=runtime,
                conversation_store=conversation_store,
                trace_handler=trace_handler,
                conversation=conversation,
                provider=provider,
                history=history,
                content=args.message,
                model=model,
                summary_store=summary_store,
            )
            return 0 if success else 1

        print(
            "命令：/new 新建会话，/sessions 查看会话，/use <id> 切换会话，"
            "/runs 查看运行，/checkpoints 查看恢复点，/trace <id> 查看轨迹，"
            "/permissions 查看审批规则，"
            "/clear 清空当前会话，/help 查看帮助，/exit 退出"
        )
        while True:
            try:
                content = input("\n你> ").strip()
            except EOFError, KeyboardInterrupt:
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
                _print_runs(
                    await trace_store.list_runs(),
                    conversation.id,
                )
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
                _print_recovered_checkpoints(
                    await checkpoint_store.recover_running(
                        conversation_id=conversation.id,
                    )
                )
                print(
                    f"已切换会话：{conversation.id[:8]} · "
                    f"{conversation.title} · {conversation.message_count} 条消息"
                )
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
                print(
                    "/new [标题] 新建会话\n"
                    "/sessions 查看最近会话\n"
                    "/use <会话ID> 切换会话\n"
                    "/runs 查看最近 Agent Run\n"
                    "/checkpoints 查看当前会话的运行恢复点\n"
                    "/trace <Run ID> 查看完整事件轨迹\n"
                    "/permissions 查看当前会话的审批规则\n"
                    "/permission remove <规则ID> 删除一条审批规则\n"
                    "/permissions clear 清除当前会话的全部审批规则\n"
                    "/clear 清空当前会话\n"
                    "/exit 退出聊天"
                )
                continue

            _, conversation = await _send_message(
                runtime=runtime,
                conversation_store=conversation_store,
                trace_handler=trace_handler,
                conversation=conversation,
                provider=provider,
                history=history,
                content=content,
                model=model,
                summary_store=summary_store,
            )
    finally:
        await registry.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Chat with a configured OneAgent model provider."
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
        default=(
            "你是 OneAgent，一个本地运行的智能助理。请使用用户的语言回答。"
            f"当前日期是 {datetime.now().astimezone().date().isoformat()}。"
            "调用工具时优先使用已有结果；网页搜索通常只需一到两次，获得可用结果后"
            "立即整理回答，不要为了追求完美而反复改写相同查询。"
            "当用户明确要求记录任务，或工作复杂、需要多个步骤或跨多轮跟踪时，"
            "调用 task_create；简单的一次性问题不要创建任务。完成任务步骤、计划"
            "变化或任务状态变化后调用 task_update，必要时用 task_get/task_list"
            "重新确认任务状态。"
        ),
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
        default=3,
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
