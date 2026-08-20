"""macOS Computer V5 - 文本输入手动 demo（真实 CGEvent Unicode 输入）。

这是可选手动集成测试，**不会**在 pytest / swift test 里自动执行。
需要先授予 Accessibility 权限（系统设置 → 隐私与安全性 → 辅助功能），
并先把光标放到一个可输入位置（如 TextEdit 编辑区）。

用法：
    cd native/macos-computer-helper && swift build
    cd backend
    PYTHONPATH=. .venv/bin/python scripts/computer_type_demo.py \
        ../native/macos-computer-helper/.build/debug/MacOSComputerHelper \
        "Hello oneAgent 你好"
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.computer import MacOSComputerRuntime, MacOSHelperClient


async def main(helper_path: Path, text: str) -> None:
    client = MacOSHelperClient(helper_path)
    runtime = MacOSComputerRuntime(client)
    await runtime.start()
    try:
        status = await client.call("accessibility_status", {})
        if status.get("trusted") is not True:
            print(
                "未授予 Accessibility 权限：请先在 "
                "系统设置 → 隐私与安全性 → 辅助功能 授权，"
                "并把光标放到可输入位置（如 TextEdit）后重试。"
            )
            return
        result = await runtime.type(text)
        print("type result:", result.model_dump(mode="json"))
    finally:
        await runtime.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="向当前焦点输入文本（CGEvent Unicode）。"
    )
    parser.add_argument(
        "helper_path",
        type=Path,
        help="Swift helper 二进制路径（.build/debug/MacOSComputerHelper）",
    )
    parser.add_argument(
        "text",
        nargs="?",
        default="Hello oneAgent 你好",
        help="要输入的文本（默认 Hello oneAgent 你好）",
    )
    args = parser.parse_args()
    asyncio.run(main(args.helper_path.expanduser().resolve(), args.text))
