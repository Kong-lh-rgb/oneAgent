"""macOS Computer V1 - Open App 手动验收 demo（真实打开 App）。

这是可选手动集成测试，**不会**在 pytest / swift test 里自动执行
（避免自动化测试弹应用）。

用法：
    cd native/macos-computer-helper && swift build
    cd backend
    PYTHONPATH=. .venv/bin/python scripts/computer_open_app_demo.py \
        ../native/macos-computer-helper/.build/debug/MacOSComputerHelper \
        --app TextEdit
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.computer import MacOSComputerRuntime, MacOSHelperClient


async def main(helper_path: Path, app: str) -> None:
    client = MacOSHelperClient(helper_path)
    runtime = MacOSComputerRuntime(client)
    await runtime.start()
    try:
        result = await runtime.open_app(app)
        print(result.model_dump(mode="json"))
    finally:
        await runtime.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="通过完整链路真实打开一个 macOS App。"
    )
    parser.add_argument(
        "helper_path",
        type=Path,
        help="Swift helper 二进制路径（.build/debug/MacOSComputerHelper）",
    )
    parser.add_argument(
        "--app",
        default="TextEdit",
        help="应用名称或 bundle id（默认 TextEdit）",
    )
    args = parser.parse_args()
    asyncio.run(main(args.helper_path.expanduser().resolve(), args.app))
