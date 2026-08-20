"""oneAgent Host 入口：``python -m app.server``。"""

from __future__ import annotations

import argparse
import logging
import sys


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the oneAgent Host (FastAPI + JSON-RPC WebSocket)."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--database",
        help="SQLite database path (default: backend/.oneagent/oneagent.db).",
    )
    parser.add_argument("--provider", help="Model provider (default: auto-select).")
    parser.add_argument("--model", help="Override the configured model name.")
    parser.add_argument(
        "--mcp-config",
        help="Path to the MCP Server JSON configuration file.",
    )
    parser.add_argument(
        "--computer-helper",
        help="Explicit Swift helper binary path (overrides env/dev auto-detect).",
    )
    parser.add_argument(
        "--disable-computer",
        action="store_true",
        help="Disable Computer Runtime even if a helper is available.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    import uvicorn

    from app.application import Application

    # 仅在显式提供时才覆盖默认路径，避免 None 覆盖 composition root 的默认值。
    application_kwargs: dict[str, object] = {}
    if args.database:
        application_kwargs["database"] = args.database
    if args.mcp_config:
        application_kwargs["mcp_config"] = args.mcp_config

    # 默认 Host 接入真实 MacOSComputerRuntime（helper 找不到 / disabled 不影响启动）。
    from app.computer import build_macos_computer

    computer_runtime, computer_host_status = build_macos_computer(
        helper_path=args.computer_helper,
        enabled=False if args.disable_computer else None,
    )
    application_kwargs["computer_runtime"] = computer_runtime
    application_kwargs["computer_host_status"] = computer_host_status

    try:
        application = Application(
            provider=args.provider,
            model=args.model,
            **application_kwargs,
        )
    except ValueError as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 2

    from app.server.app import create_app

    app = create_app(application)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
