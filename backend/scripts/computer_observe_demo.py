"""macOS Computer V2 - Observe 手动验收 demo。

这是可选手动集成测试，**不会**在 pytest / swift test 里自动执行。

先打印 Accessibility 权限状态；已授权则读取当前前台 App / 窗口并打印
Observation，未授权则提示需要授权。

用法：
    cd native/macos-computer-helper && swift build
    cd backend
    PYTHONPATH=. .venv/bin/python scripts/computer_observe_demo.py \
        ../native/macos-computer-helper/.build/debug/MacOSComputerHelper

授权后重跑即可读到真实前台窗口。
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.computer import MacOSComputerRuntime, MacOSHelperClient


async def main(helper_path: Path) -> None:
    client = MacOSHelperClient(helper_path)
    runtime = MacOSComputerRuntime(client)
    await runtime.start()
    try:
        status = await client.call("accessibility_status", {})
        print("accessibility:", status)
        if status.get("trusted") is not True:
            print(
                "未授予 Accessibility 权限：请在 系统设置 → 隐私与安全性 → "
                "辅助功能 中授权后重试。"
            )
            return
        obs = await runtime.observe()
        print(obs.model_dump(mode="json"))
    finally:
        await runtime.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="读取当前前台 App / 窗口（Accessibility + Basic Observe）。"
    )
    parser.add_argument(
        "helper_path",
        type=Path,
        help="Swift helper 二进制路径（.build/debug/MacOSComputerHelper）",
    )
    args = parser.parse_args()
    asyncio.run(main(args.helper_path.expanduser().resolve()))
