"""通过已配置的模型适配器进行交互式多轮聊天。

在 backend 目录运行：

    .venv/bin/python -m app.models.chat
    .venv/bin/python -m app.models.chat --provider qwen
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.agent.runtime import AgentRuntime
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
    provider: ModelProvider,
    history: list[Message],
    content: str,
    model: str,
) -> bool:
    print("OneAgent 正在思考...", flush=True)

    result = await runtime.run(content, history=history)
    history[:] = result.messages
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
    return result.ok


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
    history = _initial_history(args.system)
    try:
        if args.message is not None:
            success = await _send_message(
                runtime=runtime,
                provider=provider,
                history=history,
                content=args.message,
                model=model,
            )
            return 0 if success else 1

        print("命令：/clear 清空上下文，/help 查看帮助，/exit 退出")
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
            if content == "/clear":
                history = _initial_history(args.system)
                print("上下文已清空。")
                continue
            if content == "/help":
                print("/clear 清空上下文\n/exit 退出聊天")
                continue

            await _send_message(
                runtime=runtime,
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
    args = parser.parse_args()
    if args.max_output_tokens <= 0:
        parser.error("--max-output-tokens must be greater than zero")
    if args.max_steps <= 0:
        parser.error("--max-steps must be greater than zero")
    return args


def main() -> None:
    raise SystemExit(asyncio.run(_run(_parse_args())))


if __name__ == "__main__":
    main()
