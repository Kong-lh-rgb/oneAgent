"""macOS Computer V4 - 语义点击手动 demo（真实触发 AXPress）。

这是可选手动集成测试，**不会**在 pytest / swift test 里自动执行。
需要先授予 Accessibility 权限（系统设置 → 隐私与安全性 → 辅助功能）。

用法：
    cd native/macos-computer-helper && swift build
    cd backend
    PYTHONPATH=. .venv/bin/python scripts/computer_click_demo.py \
        ../native/macos-computer-helper/.build/debug/MacOSComputerHelper

行为：
    observe → 打印 actions 含 press 的元素 → 对第一个可 press 元素 click →
    再用旧 observation 点一次验证 stale_observation。
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.computer import (
    ComputerHelperError,
    ElementTarget,
    MacOSComputerRuntime,
    MacOSHelperClient,
)


async def main(helper_path: Path) -> None:
    client = MacOSHelperClient(helper_path)
    runtime = MacOSComputerRuntime(client)
    await runtime.start()
    try:
        status = await client.call("accessibility_status", {})
        if status.get("trusted") is not True:
            print(
                "未授予 Accessibility 权限：请先在 "
                "系统设置 → 隐私与安全性 → 辅助功能 授权。"
            )
            return

        obs = await runtime.observe()
        pressable = [e for e in obs.elements if "press" in e.actions]
        print(f"observation_id: {obs.id}")
        print(
            f"共 {len(obs.elements)} 个元素，其中 {len(pressable)} 个可 press："
        )
        for e in pressable[:10]:
            print(f"  {e.ref} role={e.role} title={e.title!r}")

        if not pressable:
            print(
                "没有可 press 的元素，请打开一个带按钮的应用"
                "（如 系统设置 / 计算器）。"
            )
            return

        target = pressable[0]
        print(f"\n点击 {target.ref}（{target.role} {target.title!r}）...")
        result = await runtime.click(
            ElementTarget(observation_id=obs.id, element_ref=target.ref)
        )
        print("click result:", result.model_dump(mode="json"))

        print("\n用旧 observation 再次点击（应返回 stale_observation）：")
        try:
            await runtime.click(
                ElementTarget(observation_id=obs.id, element_ref=target.ref)
            )
            print("（意外成功）")
        except ComputerHelperError as exc:
            print(f"符合预期：{exc}")
    finally:
        await runtime.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="真实触发一次 AXPress 语义点击。"
    )
    parser.add_argument(
        "helper_path",
        type=Path,
        help="Swift helper 二进制路径（.build/debug/MacOSComputerHelper）",
    )
    args = parser.parse_args()
    asyncio.run(main(args.helper_path.expanduser().resolve()))
