"""macOS Computer V7 统一手动验收入口；不会被自动测试调用。"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.computer import CoordinateTarget, MacOSComputerRuntime, MacOSHelperClient


async def run(args: argparse.Namespace) -> None:
    runtime = MacOSComputerRuntime(MacOSHelperClient(args.helper_path))
    await runtime.start()
    try:
        print(
            "Accessibility:",
            await runtime.helper_client.call("accessibility_status", {"prompt": False}),
        )
        print(
            "Screen Recording:",
            await runtime.helper_client.call(
                "screen_capture_status", {"prompt": args.prompt_screen_permission}
            ),
        )
        if args.app:
            print((await runtime.open_app(args.app)).model_dump(mode="json"))
            await asyncio.sleep(1)
        observation = None
        if args.observe or args.focus or args.coordinate_click or args.scroll:
            observation = await runtime.observe(
                include_screenshot=not args.no_screenshot
            )
            print(observation.model_dump_json(indent=2))
        if args.focus:
            print((await runtime.focus_window(args.focus)).model_dump(mode="json"))
        if args.scroll:
            print(
                (
                    await runtime.scroll(delta_x=args.scroll[0], delta_y=args.scroll[1])
                ).model_dump(mode="json")
            )
        if args.coordinate_click:
            assert observation is not None
            x, y = args.coordinate_click
            print(
                (
                    await runtime.click(
                        CoordinateTarget(observation_id=observation.id, x=x, y=y)
                    )
                ).model_dump(mode="json")
            )
        if args.focus or args.scroll or args.coordinate_click:
            print(
                (
                    await runtime.observe(include_screenshot=not args.no_screenshot)
                ).model_dump_json(indent=2)
            )
    finally:
        await runtime.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Computer V7 手动 E2E")
    parser.add_argument("helper_path", type=Path)
    parser.add_argument("--app", help="可选：先打开应用")
    parser.add_argument("--observe", action="store_true")
    parser.add_argument("--scroll", nargs=2, type=int, metavar=("DX", "DY"))
    parser.add_argument("--focus", metavar="WINDOW_REF")
    parser.add_argument("--coordinate-click", nargs=2, type=int, metavar=("X", "Y"))
    parser.add_argument("--no-screenshot", action="store_true")
    parser.add_argument("--prompt-screen-permission", action="store_true")
    options = parser.parse_args()
    options.helper_path = options.helper_path.expanduser().resolve()
    asyncio.run(run(options))
