"""进程内 owner + flock 跨 Host 互斥的 Computer Machine Lease。"""

from __future__ import annotations

import fcntl
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

from app.tools.hooks import ToolExecutionContext, ToolHook, ToolHookDecision

logger = logging.getLogger("oneagent.computer.lease")


class ComputerBusyError(RuntimeError):
    """电脑已被其它 Run 或 Host 占用。"""


@dataclass(frozen=True, slots=True)
class ComputerLeaseSnapshot:
    owner_run_id: str | None
    acquired_at: datetime | None
    process_id: int


class ComputerLeaseManager:
    """以 run_id 为 owner 的非等待、不可抢占机器租约。"""

    def __init__(self, lock_path: str | Path) -> None:
        self.lock_path = Path(lock_path).expanduser().resolve()
        self._guard = Lock()
        self._owner_run_id: str | None = None
        self._acquired_at: datetime | None = None
        self._lock_file = None

    @property
    def snapshot(self) -> ComputerLeaseSnapshot:
        with self._guard:
            return ComputerLeaseSnapshot(
                owner_run_id=self._owner_run_id,
                acquired_at=self._acquired_at,
                process_id=os.getpid(),
            )

    def acquire(self, run_id: str) -> ComputerLeaseSnapshot:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("computer action requires run context")
        with self._guard:
            if self._owner_run_id == run_id:
                return self.snapshot_unlocked()
            if self._owner_run_id is not None:
                logger.info("computer lease busy for run %s", run_id)
                raise ComputerBusyError(
                    "computer is currently controlled by another run"
                )

            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_file = self.lock_path.open("a+", encoding="utf-8")
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                lock_file.close()
                logger.info("computer lease busy in another host")
                raise ComputerBusyError(
                    "computer is currently controlled by another run"
                ) from exc

            acquired_at = datetime.now(UTC)
            lock_file.seek(0)
            lock_file.truncate()
            lock_file.write(
                f"pid={os.getpid()}\nrun_id={run_id}\n"
                f"acquired_at={acquired_at.isoformat()}\n"
            )
            lock_file.flush()
            self._lock_file = lock_file
            self._owner_run_id = run_id
            self._acquired_at = acquired_at
            logger.info("computer lease acquired by run %s", run_id)
            return self.snapshot_unlocked()

    def snapshot_unlocked(self) -> ComputerLeaseSnapshot:
        return ComputerLeaseSnapshot(
            owner_run_id=self._owner_run_id,
            acquired_at=self._acquired_at,
            process_id=os.getpid(),
        )

    def release(self, run_id: str) -> bool:
        with self._guard:
            if self._owner_run_id != run_id:
                return False
            self._release_unlocked()
            logger.info("computer lease released by run %s", run_id)
            return True

    def close(self) -> None:
        with self._guard:
            self._release_unlocked()

    def _release_unlocked(self) -> None:
        lock_file = self._lock_file
        self._lock_file = None
        self._owner_run_id = None
        self._acquired_at = None
        if lock_file is not None:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            finally:
                lock_file.close()


class ComputerLeaseHook(ToolHook):
    """只拦截 computer_*，生产链缺 run_id 时 fail closed。"""

    critical = True

    def __init__(self, manager: ComputerLeaseManager) -> None:
        self.manager = manager

    async def before_execute(
        self, context: ToolExecutionContext
    ) -> ToolHookDecision | None:
        if not context.tool_call.name.startswith("computer_"):
            return None
        if not context.run_id:
            return ToolHookDecision(
                denied_reason="computer action requires run context"
            )
        try:
            self.manager.acquire(context.run_id)
        except ComputerBusyError as exc:
            return ToolHookDecision(denied_reason=str(exc))
        return None


__all__ = [
    "ComputerBusyError",
    "ComputerLeaseHook",
    "ComputerLeaseManager",
    "ComputerLeaseSnapshot",
]
