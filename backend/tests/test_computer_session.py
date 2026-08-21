"""ComputerSession 生命周期与 Target-bound 语义测试（V2）。

覆盖：
- begin 建立 / 同 Run 复用；
- end 清除 Python 侧 target / snapshot；
- require_active / validate fail closed（无 session / session 不匹配）；
- cross-run isolation：Run A 的 target 绝不泄漏到 Run B；
- snapshot 失效（side-effect 后 invalidate）；
- 结构化错误码规范化。
"""

from __future__ import annotations

import pytest

from app.computer.errors import (
    STALE_SNAPSHOT,
    canonicalize,
)
from app.computer.models import ActiveApp, Bounds, Observation, Window
from app.computer.session import (
    ComputerSessionError,
    ComputerSessionManager,
    ComputerSessionMismatchError,
    ComputerSessionNotActiveError,
)


def _notes() -> ActiveApp:
    return ActiveApp(name="Notes", bundle_id="com.apple.Notes", pid=111)


def _vs_code() -> ActiveApp:
    return ActiveApp(name="Code", bundle_id="com.microsoft.VSCode", pid=222)


def _observation(session_id: str, app: ActiveApp) -> Observation:
    return Observation(
        id=session_id,
        target=app,
        user_frontmost_app=app,
        active_window=None,
        windows=(),
        elements=(),
    )


# ---------------------------------------------------------------------------
# Session 生命周期
# ---------------------------------------------------------------------------


def test_begin_creates_and_reuses_session() -> None:
    manager = ComputerSessionManager()
    first = manager.begin("run-a")
    assert first.run_id == "run-a"
    assert first.session_id

    again = manager.begin("run-a")
    assert again is first  # 同 Run 复用同一 session


def test_begin_without_run_id_fails_closed() -> None:
    manager = ComputerSessionManager()
    with pytest.raises(Exception):
        manager.begin("")


def test_end_clears_target_and_snapshot() -> None:
    manager = ComputerSessionManager()
    session = manager.begin("run-a")
    session.begin_target(_notes())
    session.attach_snapshot(_observation("obs-1", _notes()))

    assert manager.end("run-a") is True
    assert manager.get_active() is None
    assert session.target_app is None
    assert session.current_snapshot is None


def test_end_unknown_run_returns_false() -> None:
    manager = ComputerSessionManager()
    assert manager.end("nope") is False


def test_require_active_fails_closed_without_session() -> None:
    manager = ComputerSessionManager()
    with pytest.raises(ComputerSessionNotActiveError):
        manager.require_active()


def test_validate_rejects_stale_session() -> None:
    manager = ComputerSessionManager()
    first = manager.begin("run-a")
    with pytest.raises(ComputerSessionMismatchError):
        manager.validate("some-other-session-id")
    # 正确 session 放行
    assert manager.validate(first.session_id) is first


# ---------------------------------------------------------------------------
# Cross-run isolation
# ---------------------------------------------------------------------------


def test_cross_run_target_never_leaks() -> None:
    """Run A 绑定 Notes 并结束；Run B 开始后绝不能继承 Notes。"""

    manager = ComputerSessionManager()
    run_a = manager.begin("run-a")
    run_a.begin_target(_notes())
    run_a.attach_snapshot(_observation("obs-a", _notes()))
    manager.end("run-a")

    run_b = manager.begin("run-b")
    assert run_b.session_id != run_a.session_id
    assert run_b.target_app is None
    assert run_b.current_snapshot is None


def test_new_run_rejected_while_another_active() -> None:
    """已有其它 active Run 时，新 Run 的 begin 必须 fail closed（绝不接管）。"""

    manager = ComputerSessionManager()
    manager.begin("run-a")
    with pytest.raises(ComputerSessionError):
        manager.begin("run-b")
    assert manager.active_run_id == "run-a"
    assert manager.require_active().run_id == "run-a"


# ---------------------------------------------------------------------------
# Snapshot 生命周期
# ---------------------------------------------------------------------------


def test_action_invalidates_snapshot() -> None:
    manager = ComputerSessionManager()
    session = manager.begin("run-a")
    session.attach_snapshot(_observation("obs-1", _notes()))
    assert session.snapshot_id() == "obs-1"

    session.invalidate_snapshot()
    assert session.snapshot_id() is None


def test_attach_snapshot_records_exact_target_window() -> None:
    manager = ComputerSessionManager()
    session = manager.begin("run-a")
    window = Window(
        ref="w7",
        title="Document",
        bounds=Bounds(x=0, y=0, width=800, height=600),
    )
    observation = Observation(id="obs-1", active_window=window)

    session.attach_snapshot(observation)

    assert session.target_window is window


# ---------------------------------------------------------------------------
# 结构化错误码
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("legacy", "expected"),
    [
        ("stale_observation", STALE_SNAPSHOT),
        ("focus_failed", "background_action_failed"),
        ("app_activation_failed", "foreground_activation_failed"),
        ("screenshot_unavailable", "screen_capture_unavailable"),
        ("session_mismatch", "session_mismatch"),
    ],
)
def test_error_code_canonicalization(legacy: str, expected: str) -> None:
    assert canonicalize(legacy) == expected


def test_unknown_error_code_passthrough() -> None:
    assert canonicalize("something_new") == "something_new"
    assert canonicalize(None) is None
    assert canonicalize("") is None
