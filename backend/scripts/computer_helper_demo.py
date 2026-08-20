"""Computer Helper V0 验收 demo：Python ↔ Swift 长驻通信链。

用法：
    cd native/macos-computer-helper && swift build
    cd backend
    .venv/bin/python scripts/computer_helper_demo.py \
        ../native/macos-computer-helper/.build/debug/MacOSComputerHelper
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from app.computer import MacOSHelperClient


async def main(helper_path: Path) -> None:
    client = MacOSHelperClient(helper_path)
    await client.start()
    try:
        print(await client.call("ping", {}))
        print(await client.call("system_info", {}))

        print("\n并发：")
        results = await asyncio.gather(
            client.call("ping", {}),
            client.call("system_info", {}),
        )
        print(results)
    finally:
        await client.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(
            "usage: computer_helper_demo.py <helper-binary>",
            file=sys.stderr,
        )
        sys.exit(2)
    asyncio.run(main(Path(sys.argv[1]).expanduser().resolve()))
