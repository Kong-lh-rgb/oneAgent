"""通过已配置的模型适配器进行交互式多轮聊天。

在 backend 目录运行：

    .venv/bin/python -m app.models.chat
    .venv/bin/python -m app.models.chat --provider qwen
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from app.agent.events import AgentEvent, AgentEventType
from app.agent.result import AgentResult
from app.agent.runtime import AgentRuntime
from app.conversation import (
    DEFAULT_DATABASE_PATH,
    Conversation,
    SQLiteConversationStore,
)
from app.tools import ConsoleApprovalGate, build_builtin_tool_registry

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
    conversation: Conversation,
    provider: ModelProvider,
    history: list[Message],
    content: str,
    model: str,
) -> tuple[bool, Conversation]:
    print("OneAgent 正在思考...", flush=True)

    result: AgentResult | None = None
    async for event in runtime.run_stream(
        content,
        history=history,
        conversation_id=conversation.id,
    ):
        _print_agent_event(event)
        if event.result is not None:
            result = event.result

    if result is None:
        raise RuntimeError("Agent 事件流结束时缺少最终结果")
    history[:] = result.messages
    conversation = await conversation_store.replace_messages(
        conversation.id,
        history,
    )
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
            f"{record.tool_call.name}"
            f"({'成功' if record.result.success else '失败'})"
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
    registry = ModelAdapterRegistry(settings)
    runtime = AgentRuntime(
        registry,
        build_builtin_tool_registry(),
        provider=provider,
        model=args.model,
        max_steps=args.max_steps,
        max_output_tokens=args.max_output_tokens,
        approval_gate=ConsoleApprovalGate(),
    )
    try:
        if args.message is not None:
            success, conversation = await _send_message(
                runtime=runtime,
                conversation_store=conversation_store,
                conversation=conversation,
                provider=provider,
                history=history,
                content=args.message,
                model=model,
            )
            return 0 if success else 1

        print(
            "命令：/new 新建会话，/sessions 查看会话，/use <id> 切换会话，"
            "/clear 清空当前会话，/help 查看帮助，/exit 退出"
        )
        while True:
            try:
                content = input("\n你> ").strip()
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
                history = list(
                    await conversation_store.load_messages(conversation.id)
                )
                print(f"已创建会话：{conversation.id[:8]} · {conversation.title}")
                continue
            if content == "/sessions":
                _print_conversations(
                    await conversation_store.list(),
                    conversation.id,
                )
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
                history = list(
                    await conversation_store.load_messages(conversation.id)
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
                print("上下文已清空。")
                continue
            if content == "/help":
                print(
                    "/new [标题] 新建会话\n"
                    "/sessions 查看最近会话\n"
                    "/use <会话ID> 切换会话\n"
                    "/clear 清空当前会话\n"
                    "/exit 退出聊天"
                )
                continue

            _, conversation = await _send_message(
                runtime=runtime,
                conversation_store=conversation_store,
                conversation=conversation,
                provider=provider,
                history=history,
                content=content,
                model=model,
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
        default="你是 OneAgent，一个本地运行的智能助理。请使用用户的语言回答。",
        help="System prompt for this conversation.",
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=1024,
        help="Maximum output tokens for each reply.",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=10,
        help="Maximum model/tool loop steps for each message.",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help="SQLite conversation database path.",
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
    if args.max_output_tokens <= 0:
        parser.error("--max-output-tokens must be greater than zero")
    if args.max_steps <= 0:
        parser.error("--max-steps must be greater than zero")
    if args.conversation and args.new_conversation:
        parser.error("--conversation and --new-conversation cannot be used together")
    return args


def main() -> None:
    raise SystemExit(asyncio.run(_run(_parse_args())))


if __name__ == "__main__":
    main()
