"""Shell 命令执行工具（需人工审核）。

危险操作，权限档位为 HUMAN_APPROVAL：模型可以申请调用，
但执行前必须经过 ApprovalGate 的人工确认。
"""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path
from time import perf_counter
from typing import Any

from app.models.types import ToolDefinition, ToolPermission

from ..base import BaseTool
from ._workspace import resolve_workspace_path, workspace_root_path

MAX_SHELL_TIMEOUT_SECONDS = 120.0


class ShellCommandTool(BaseTool):
    def __init__(self, workspace_root: str | Path | None = None) -> None:
        self._workspace_root = workspace_root_path(workspace_root)

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="run_shell_command",
            description=(
                "Run a shell command inside the local workspace and return "
                "its exit code, stdout, and stderr. Requires human approval."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute.",
                    },
                    "working_directory": {
                        "type": "string",
                        "description": (
                            "Optional subdirectory (relative to the workspace) "
                            "in which to run the command. Defaults to the "
                            "workspace root."
                        ),
                    },
                    "timeout_seconds": {
                        "type": "number",
                        "description": (
                            "Maximum seconds to wait before terminating. "
                            f"Capped at {MAX_SHELL_TIMEOUT_SECONDS:g}."
                        ),
                    },
                },
                "required": ["command"],
                "additionalProperties": False,
            },
            strict=True,
            permission=ToolPermission.HUMAN_APPROVAL,
        )

    async def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ValueError("'command' must be a non-empty string")

        cwd = self._workspace_root
        raw_directory = arguments.get("working_directory")
        if raw_directory is not None:
            if not isinstance(raw_directory, str):
                raise ValueError("'working_directory' must be a string")
            cwd = resolve_workspace_path(
                self._workspace_root,
                raw_directory,
                allow_root=True,
            )

        timeout = arguments.get("timeout_seconds", 30.0)
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ValueError("'timeout_seconds' must be a positive number")
        timeout = min(float(timeout), MAX_SHELL_TIMEOUT_SECONDS)

        started_at = perf_counter()
        timed_out = False
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd),
            start_new_session=True,
        )
        try:
            async with asyncio.timeout(timeout):
                stdout_bytes, stderr_bytes = await process.communicate()
        except TimeoutError:
            timed_out = True
            _terminate_process(process)
            stdout_bytes, stderr_bytes = await process.communicate()

        duration_ms = (perf_counter() - started_at) * 1000
        return {
            "command": command,
            "working_directory": cwd.as_posix(),
            "exit_code": process.returncode,
            "timed_out": timed_out,
            "stdout": stdout_bytes.decode("utf-8", errors="replace"),
            "stderr": stderr_bytes.decode("utf-8", errors="replace"),
            "duration_ms": round(duration_ms, 3),
        }


def _terminate_process(process: asyncio.subprocess.Process) -> None:
    """终止整个进程组，避免遗留子进程。"""
    try:
        if hasattr(os, "killpg"):
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        else:  # pragma: no cover - Windows
            process.kill()
    except (ProcessLookupError, PermissionError):
        pass
