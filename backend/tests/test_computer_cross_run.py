"""Cross-run 隔离的 runtime+session 集成测试（V2）。

证明：Run A 绑定 Notes 并结束（end_session 通知 helper 清 Native Target）后，
Run B 第一次 observe 使用全新 session，绝不会继承 Notes。
"""

from __future__ import annotations

import pytest

from app.computer.macos import MacOSComputerRuntime


class RecordingHelper:
    """记录 call 的 helper stub；open_app 返回 Notes，observe 返回 frontmost。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.end_session_calls: list[str] = []
        self.observe_count = 0

    async def ensure_started(self) -> None:
        return None

    async def call(self, method: str, params: dict | None = None, **kwargs):
        params = params or {}
        self.calls.append((method, params))
        if method == "open_app":
            return {
                "app": "Notes",
                "bundle_id": "com.apple.Notes",
                "process_id": 111,
                "activation_status": "not_frontmost",
            }
        if method == "observe":
            self.observe_count += 1
            return {
                "active_app": {"name": "Code", "process_id": 222},
                "target": {"name": "Code", "process_id": 222},
                "target_is_frontmost": True,
                "user_frontmost_app": {"name": "Code", "process_id": 222},
                "windows": [],
                "elements": [],
                "element_stats": {},
                "truncated": False,
            }
        if method == "end_session":
            self.end_session_calls.append(params.get("session_id", ""))
            return {"ended": True}
        return {}


@pytest.mark.asyncio
async def test_run_a_end_then_run_b_never_inherits_notes() -> None:
    helper = RecordingHelper()
    runtime = MacOSComputerRuntime(helper)  # type: ignore[arg-type]

    # --- Run A：open_app Notes → target = Notes ---
    run_a = runtime.begin_session("run-a")
    await runtime.open_app("Notes")
    assert run_a.target_app is not None
    assert run_a.target_app.name == "Notes"
    assert run_a.target_app.pid == 111

    # --- Run A 结束：end_session 通知 helper 清 Native Target ---
    await runtime.end_session("run-a")
    assert helper.end_session_calls == [run_a.session_id]
    assert runtime._session_manager.get_active() is None

    # --- Run B：全新 session，第一次 observe 绝不继承 Notes ---
    run_b = runtime.begin_session("run-b")
    assert run_b.session_id != run_a.session_id
    assert run_b.target_app is None

    observation = await runtime.observe(include_screenshot=False)
    # observe 从 helper 返回的是当前 frontmost（Code），不是 Notes。
    assert observation.target is not None
    assert observation.target.name != "Notes"
    assert run_b.target_app is not None
    assert run_b.target_app.name == "Code"


@pytest.mark.asyncio
async def test_end_session_is_idempotent_and_clears_python_state() -> None:
    helper = RecordingHelper()
    runtime = MacOSComputerRuntime(helper)  # type: ignore[arg-type]
    runtime.begin_session("run-a")
    assert await runtime.end_session("run-a") is True
    assert await runtime.end_session("run-a") is False  # 第二次幂等
