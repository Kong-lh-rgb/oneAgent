"""手动测试工具层与 Agent 运行时的演示脚本。

在 backend 目录运行：

    # 1) 直接测试 6 个内置工具（read/write/list 免审批；shell/http/search 会弹人工审批）
    .venv/bin/python scripts/demo_tools.py --direct

    # 2) 用真实模型跑 AgentRuntime（含人工审批 + 工具执行记录）
    .venv/bin/python scripts/demo_tools.py \
        --agent "读取 workspace 下的文件，然后用 web 搜索 OneAgent"
    .venv/bin/python scripts/demo_tools.py --agent "..." --provider qwen
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

# 让 `python scripts/demo_tools.py` 从任意目录运行时都能导入 backend 根目录的 app 包
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.runtime import AgentRuntime
from app.models.config import ModelSettings
from app.models.registry import ModelAdapterRegistry
from app.models.types import ToolCall
from app.tools import (
    ConsoleApprovalGate,
    ToolExecutor,
    build_builtin_tool_registry,
)


def _print_result(result: Any) -> None:
    print(
        f"\n[{result.tool_name}] success={result.success} "
        f"duration={result.duration_ms:.1f}ms"
    )
    if result.success:
        output = json.loads(result.output or "{}")
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(f"  ERROR: {result.error}")


async def _demo_direct() -> None:
    registry = build_builtin_tool_registry()
    executor = ToolExecutor(registry, approval_gate=ConsoleApprovalGate())

    print("===== 1. 免审批工具：write_file / read_file / list_files =====")
    await executor.execute(
        ToolCall(
            id="w1",
            name="write_file",
            arguments={
                "path": "demo/hello.txt",
                "content": "OneAgent 本地工具测试",
            },
        )
    )
    await executor.execute(
        ToolCall(
            id="r1",
            name="read_file",
            arguments={"path": "demo/hello.txt"},
        )
    )
    await executor.execute(
        ToolCall(
            id="l1",
            name="list_files",
            arguments={"directory": "demo"},
        )
    )

    print("\n===== 2. 需审批工具：run_shell_command（弹 y/N）=====")
    await executor.execute(
        ToolCall(
            id="s1",
            name="run_shell_command",
            arguments={"command": "echo hello from shell && pwd"},
        )
    )

    print("\n===== 3. 需审批工具：http_request（弹 y/N）=====")
    await executor.execute(
        ToolCall(
            id="h1",
            name="http_request",
            arguments={"url": "https://example.com/", "method": "GET"},
        )
    )

    print("\n===== 4. 需审批工具：web_search（弹 y/N）=====")
    await executor.execute(
        ToolCall(
            id="q1",
            name="web_search",
            arguments={"query": "OneAgent", "max_results": 3},
        )
    )

    print("\n===== 可观测性：全部执行记录 =====")
    for record in executor.execution_records:
        status = "OK  " if record.success else "FAIL"
        print(
            f"  [{status}] {record.tool_name:<18} "
            f"perm={record.permission:<14} {record.duration_ms:>8.1f}ms"
            f"  error={record.error}"
        )


async def _demo_agent(query: str, provider: str | None) -> None:
    settings = ModelSettings()
    model_registry = ModelAdapterRegistry(settings)
    runtime = AgentRuntime(
        model_registry,
        build_builtin_tool_registry(),
        provider=provider,
        approval_gate=ConsoleApprovalGate(),
    )
    try:
        result = await runtime.run(query)
        print(f"\n=== Agent 最终回复 ===\n{result.content}\n")
        print("=== 工具执行记录 ===")
        for record in runtime.tool_records:
            status = "OK  " if record.success else "FAIL"
            print(
                f"  [{status}] {record.tool_name:<18} "
                f"perm={record.permission:<14} {record.duration_ms:>8.1f}ms"
                f"  error={record.error}"
            )
    finally:
        await model_registry.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OneAgent 工具层演示")
    parser.add_argument(
        "--direct",
        action="store_true",
        help="直接测试内置工具",
    )
    parser.add_argument(
        "--agent",
        metavar="QUERY",
        help="用真实模型跑 AgentRuntime",
    )
    parser.add_argument(
        "--provider",
        help="指定模型提供商（openai/qwen/deepseek/anthropic）",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.agent:
        raise SystemExit(asyncio.run(_demo_agent(args.agent, args.provider)))
    raise SystemExit(asyncio.run(_demo_direct()))


if __name__ == "__main__":
    main()
