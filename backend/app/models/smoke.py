"""Live smoke test for configured model providers.

Run from the backend directory:

    .venv/bin/python -m app.models.smoke
    .venv/bin/python -m app.models.smoke --provider qwen
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from time import perf_counter

from .config import ModelSettings
from .registry import ModelAdapterRegistry
from .types import Message, MessageRole, ModelProvider, ModelRequest


async def _test_provider(
    registry: ModelAdapterRegistry,
    provider: ModelProvider,
    *,
    prompt: str,
    max_output_tokens: int,
) -> bool:
    started_at = perf_counter()
    try:
        adapter = registry.get(provider)
        response = await adapter.complete(
            ModelRequest(
                messages=(Message(role=MessageRole.USER, content=prompt),),
                max_output_tokens=max_output_tokens,
            )
        )
    except Exception as exc:
        elapsed = perf_counter() - started_at
        print(
            f"FAIL {provider.value} ({elapsed:.2f}s): {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return False

    elapsed = perf_counter() - started_at
    content = response.message.content or "<empty text response>"
    print(
        f"PASS {provider.value} model={response.model} "
        f"latency={elapsed:.2f}s tokens={response.usage.total_tokens}"
    )
    print(f"  {content.strip()}")
    return True


async def _run(args: argparse.Namespace) -> int:
    settings = ModelSettings()
    configured = settings.configured_providers()

    if args.provider == "all":
        providers = configured
    else:
        requested = ModelProvider(args.provider)
        if requested not in configured:
            print(
                f"Provider '{requested.value}' is not configured in backend/.env.",
                file=sys.stderr,
            )
            return 2
        providers = (requested,)

    if not providers:
        print(
            "No model provider is configured in backend/.env.",
            file=sys.stderr,
        )
        return 2

    print("Testing providers:", ", ".join(provider.value for provider in providers))
    registry = ModelAdapterRegistry(settings)
    try:
        results = [
            await _test_provider(
                registry,
                provider,
                prompt=args.prompt,
                max_output_tokens=args.max_output_tokens,
            )
            for provider in providers
        ]
    finally:
        await registry.close()

    return 0 if all(results) else 1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send a minimal live request to configured model providers."
    )
    parser.add_argument(
        "--provider",
        choices=["all", *(provider.value for provider in ModelProvider)],
        default="all",
        help="Provider to test; defaults to every configured provider.",
    )
    parser.add_argument(
        "--prompt",
        default="只回复：连接成功",
        help="Minimal prompt sent to the provider.",
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=32,
        help="Maximum output tokens per request.",
    )
    args = parser.parse_args()
    if args.max_output_tokens <= 0:
        parser.error("--max-output-tokens must be greater than zero")
    return args


def main() -> None:
    raise SystemExit(asyncio.run(_run(_parse_args())))


if __name__ == "__main__":
    main()
