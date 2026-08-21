"""Run-scoped ComputerSession：把 Computer 状态归属到单个 Run。

生命周期：
    Run 内第一次 computer_* tool
        → acquire Machine Lease
        → begin_session(run_id)
    同 Run 后续复用同一 session。
    Run terminal（COMPLETED / FAILED / CANCELLED / INTERRUPTED）
        → end_session(run_id) → 清除 Python 侧 target / snapshot
        → release Machine Lease

不持久化到 SQLite，纯运行期状态。

由于 Machine Lease 保证同一时间只有一个 Run 控制 Computer，进程内只维护
一个 active session；所有请求必须携带/校验 session_id，旧 session 的请求
必须 fail closed（见 ``ComputerSessionManager.validate``）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock
from uuid import uuid4

from .models import ActiveApp, Observation, Window

logger = logging.getLogger("vesta.computer.session")


class ComputerSessionError(RuntimeError):
    """Session 生命周期错误（fail closed）。"""

    code = "session_error"


class ComputerSessionNotActiveError(ComputerSessionError):
    """没有 active session，但请求需要 session 上下文。"""

    code = "session_not_active"


class ComputerSessionMismatchError(ComputerSessionError):
    """请求携带的 session_id 与当前 active session 不一致。"""

    code = "session_mismatch"


@dataclass
class ComputerSession:
    """一次 Run 的 Computer 状态（运行期，不落库）。"""

    session_id: str
    run_id: str
    # Target = Agent 正在操作的应用；User Frontmost = 用户当前使用的应用。
    # 两者可以不同。
    target_app: ActiveApp | None = None
    target_window: Window | None = None
    current_snapshot: Observation | None = None
    previous_user_focus: ActiveApp | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def begin_target(self, app: ActiveApp) -> None:
        """显式绑定 target（computer_open_app 或初始 observe 写入）。"""

        self.target_app = app

    def attach_snapshot(self, observation: Observation) -> None:
        """记录最近一次 Observation（作为 Snapshot 生命周期依据）。"""

        self.current_snapshot = observation
        self.target_window = observation.active_window

    def invalidate_snapshot(self) -> None:
        """任何产生 UI side effect 的 Action 后使 Snapshot 失效。"""

        self.current_snapshot = None

    def snapshot_id(self) -> str | None:
        return self.current_snapshot.id if self.current_snapshot else None


class ComputerSessionManager:
    """管理 active session；同一时间只有一个 Run 控制 Computer。

    线程安全（Host 的异步任务可能从不同线程访问）。
    """

    def __init__(self) -> None:
        self._guard = Lock()
        self._sessions: dict[str, ComputerSession] = {}
        self._active_run_id: str | None = None

    @property
    def active_run_id(self) -> str | None:
        with self._guard:
            return self._active_run_id

    def begin(self, run_id: str) -> ComputerSession:
        """为 Run 建立/复用 session。

        单 Run 约束由 Machine Lease 保证；这里做二次防御：
        如果已有不同 Run 的 active session，fail closed。
        """

        if not isinstance(run_id, str) or not run_id.strip():
            raise ComputerSessionError("computer session requires run context")
        with self._guard:
            existing = self._sessions.get(run_id)
            if existing is not None:
                if self._active_run_id != run_id:
                    self._active_run_id = run_id
                return existing
            if self._active_run_id is not None:
                # Machine Lease 是第一层保护；Session 本身仍必须 fail closed：
                # 已有其它 active Run 时绝不偷偷 pop 旧 session 让新 Run 接管。
                logger.warning(
                    "computer session active for another run %s; "
                    "begin %s rejected",
                    self._active_run_id,
                    run_id,
                )
                raise ComputerSessionError(
                    "computer session is already active for another run; "
                    "end the previous session first"
                )
            session = ComputerSession(
                session_id=uuid4().hex,
                run_id=run_id,
            )
            self._sessions[run_id] = session
            self._active_run_id = run_id
            logger.info(
                "computer session begun: %s (run %s)",
                session.session_id,
                run_id,
            )
            return session

    def get(self, run_id: str) -> ComputerSession | None:
        with self._guard:
            return self._sessions.get(run_id)

    def get_active(self) -> ComputerSession | None:
        with self._guard:
            if self._active_run_id is None:
                return None
            return self._sessions.get(self._active_run_id)

    def require_active(self) -> ComputerSession:
        session = self.get_active()
        if session is None:
            raise ComputerSessionNotActiveError(
                "computer session is not active; "
                "a computer_* tool must run inside an active run"
            )
        return session

    def validate(self, session_id: str) -> ComputerSession:
        """校验请求携带的 session_id 是当前 active session；否则 fail closed。"""

        session = self.require_active()
        if session.session_id != session_id:
            raise ComputerSessionMismatchError(
                f"session mismatch: request {session_id} != active {session.session_id}"
            )
        return session

    def end(self, run_id: str) -> bool:
        """结束 Run 的 session，清除 target / snapshot（Python 侧状态）。"""

        with self._guard:
            session = self._sessions.pop(run_id, None)
            if session is None:
                return False
            if self._active_run_id == run_id:
                self._active_run_id = None
            session.current_snapshot = None
            session.target_app = None
            session.target_window = None
            session.previous_user_focus = None
            logger.info(
                "computer session ended: %s (run %s)",
                session.session_id,
                run_id,
            )
            return True

    def close(self) -> None:
        with self._guard:
            self._sessions.clear()
            self._active_run_id = None

    def active_count(self) -> int:
        with self._guard:
            return len(self._sessions)


__all__ = [
    "ComputerSession",
    "ComputerSessionError",
    "ComputerSessionManager",
    "ComputerSessionMismatchError",
    "ComputerSessionNotActiveError",
]
