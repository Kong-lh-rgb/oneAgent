"""Computer V8 Machine Lease 手动安全验收，不执行真实电脑操作。"""

from __future__ import annotations

import argparse
from pathlib import Path

from app.computer import ComputerBusyError, ComputerLeaseManager


def main(lock_path: Path) -> None:
    lease = ComputerLeaseManager(lock_path)
    try:
        print("Run A acquire:", lease.acquire("manual-run-a"))
        try:
            lease.acquire("manual-run-b")
        except ComputerBusyError as exc:
            print("Run B acquire: COMPUTER_BUSY -", exc)
        print("Run A release:", lease.release("manual-run-a"))
        print("Run B acquire:", lease.acquire("manual-run-b"))
        print("Run B release:", lease.release("manual-run-b"))
    finally:
        lease.close()

    print("\nFreshness 人工场景：")
    print("1. 用 V7 demo observe TextEdit，保留 observation_id。")
    print("2. 手动切换到 Safari。")
    print("3. 批准旧 click/type；预期 helper 返回 stale_observation。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Computer V8 安全验收")
    parser.add_argument(
        "--lock-path",
        type=Path,
        default=Path(".oneagent/computer/machine.lock"),
    )
    args = parser.parse_args()
    main(args.lock_path.expanduser().resolve())
