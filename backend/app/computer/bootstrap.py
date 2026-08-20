"""macOS Computer bootstrap：解析 Swift helper 并构建真实 Runtime。

只支持 macOS。helper 找不到 / 未授权 / 显式 disable 都**不**应影响 Host
启动（Chat / Run / Automation 照常工作），只是 Computer 不可用。

helper path 解析顺序：
1. 显式参数（CLI ``--computer-helper``）；
2. 环境变量 ``VESTA_MACOS_HELPER_PATH``；
3. 开发环境自动寻找 ``native/macos-computer-helper/.build/debug/MacOSComputerHelper``。

不自动 ``swift build``：Host 启动不应依赖本机安装 Swift toolchain。
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .helper_client import MacOSHelperClient
from .macos import MacOSComputerRuntime
from .runtime import ComputerRuntime

logger = logging.getLogger("vesta.computer.bootstrap")

# backend/app/computer/bootstrap.py → 项目根
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DEV_HELPER = (
    _PROJECT_ROOT
    / "native"
    / "macos-computer-helper"
    / ".build"
    / "debug"
    / "MacOSComputerHelper"
)

ENV_HELPER_PATH = "VESTA_MACOS_HELPER_PATH"
ENV_COMPUTER_ENABLED = "VESTA_COMPUTER_ENABLED"


@dataclass(frozen=True, slots=True)
class ComputerHostStatus:
    """Computer Host 的轻量状态（不持久化，仅供 UI / 日志）。"""

    enabled: bool
    available: bool
    platform: str
    reason: str | None = None
    helper_path: str | None = None
    runtime: str | None = None


def current_platform() -> str:
    return "macos" if sys.platform == "darwin" else sys.platform


def _is_executable(path: Path) -> bool:
    try:
        return path.is_file() and os.access(path, os.X_OK)
    except OSError:
        return False


def resolve_helper_path(explicit: str | Path | None = None) -> Path | None:
    """按 显式 > 环境变量 > dev 路径 顺序解析 helper 二进制。"""

    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    env = os.environ.get(ENV_HELPER_PATH)
    if env:
        candidates.append(Path(env).expanduser())
    candidates.append(_DEFAULT_DEV_HELPER)

    for candidate in candidates:
        if _is_executable(candidate):
            return candidate.resolve()
    return None


def computer_enabled(enabled: bool | None = None) -> bool:
    """Computer 开关：显式参数 > VESTA_COMPUTER_ENABLED > 默认启用。

    默认启用（非 macOS 是否可用由 ``build_macos_computer`` 判定，不会因为
    平台不同而把用户显式启用的意图误判为 disabled）。
    """

    if enabled is not None:
        return enabled
    raw = os.environ.get(ENV_COMPUTER_ENABLED, "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return True


def build_macos_computer(
    *,
    helper_path: str | Path | None = None,
    enabled: bool | None = None,
) -> tuple[ComputerRuntime | None, ComputerHostStatus]:
    """构建真实 MacOSComputerRuntime；不可用时返回 (None, unavailable status)。"""

    platform = current_platform()
    is_enabled = computer_enabled(enabled)

    if not is_enabled:
        status = ComputerHostStatus(
            enabled=False,
            available=False,
            platform=platform,
            reason="disabled",
        )
        logger.info("Computer disabled; %s", status)
        return None, status

    if platform != "macos":
        status = ComputerHostStatus(
            enabled=True,
            available=False,
            platform=platform,
            reason="unsupported_platform",
        )
        logger.info("Computer unavailable on platform %s", platform)
        return None, status

    path = resolve_helper_path(helper_path)
    if path is None:
        status = ComputerHostStatus(
            enabled=True,
            available=False,
            platform=platform,
            reason="helper_not_found",
        )
        logger.info("Computer helper not found; Host continues without computer")
        return None, status

    client = MacOSHelperClient(path)
    runtime = MacOSComputerRuntime(client)
    status = ComputerHostStatus(
        enabled=True,
        available=True,
        platform=platform,
        reason=None,
        helper_path=str(path),
        runtime="macos",
    )
    logger.info("Computer Runtime: macOS available (%s)", path)
    return runtime, status


__all__ = [
    "ComputerHostStatus",
    "build_macos_computer",
    "computer_enabled",
    "current_platform",
    "resolve_helper_path",
]
