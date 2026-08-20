"""MacOSComputerRuntime.open_app（V1）测试。

使用 stub HelperClient，不启动真实 GUI App、不调用 NSWorkspace、
不需要 Accessibility / Screen Recording 权限。

覆盖：
1. open_app 确实发送 method=open_app / params={app}
2. helper result → ActionResult（success / action / metadata）
3. helper error 正确向上传播
4. 空 app 在 runtime 层被拒绝
5. 其它 6 个方法仍然 NotImplementedError
"""

from __future__ import annotations

import pytest

from app.computer import (
    ActionName,
    ActionResult,
    ComputerHelperError,
    MacOSComputerRuntime,
)


class StubHelperClient:
    """记录 call 并返回预设结果 / 抛预设异常的 HelperClient stub。"""

    def __init__(
        self,
        *,
        result: dict | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    async def call(self, method: str, params: dict | None = None, **kwargs):
        self.calls.append((method, params or {}))
        if self.error is not None:
            raise self.error
        return self.result if self.result is not None else {}


def _runtime(stub: StubHelperClient) -> MacOSComputerRuntime:
    return MacOSComputerRuntime(stub)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 1. open_app 调 helper
# ---------------------------------------------------------------------------


async def test_open_app_sends_request_to_helper() -> None:
    stub = StubHelperClient(
        result={
            "app": "TextEdit",
            "bundle_id": "com.apple.TextEdit",
            "process_id": 4242,
        }
    )
    runtime = _runtime(stub)

    result = await runtime.open_app("TextEdit")

    assert stub.calls == [("open_app", {"app": "TextEdit"})]
    assert result.success is True


# ---------------------------------------------------------------------------
# 2. helper result → ActionResult
# ---------------------------------------------------------------------------


async def test_open_app_converts_to_action_result() -> None:
    stub = StubHelperClient(
        result={
            "app": "TextEdit",
            "bundle_id": "com.apple.TextEdit",
            "process_id": 4242,
        }
    )
    runtime = _runtime(stub)

    result = await runtime.open_app("TextEdit")

    assert isinstance(result, ActionResult)
    assert result.success is True
    assert result.action is ActionName.OPEN_APP
    assert result.metadata["app"] == "TextEdit"
    assert result.metadata["bundle_id"] == "com.apple.TextEdit"
    assert result.metadata["process_id"] == 4242
    assert result.error is None


async def test_open_app_bundle_id_hint() -> None:
    # 以 bundle id 形式传入也要原样转发给 helper。
    stub = StubHelperClient(
        result={
            "app": "com.apple.TextEdit",
            "bundle_id": "com.apple.TextEdit",
            "process_id": 7,
        }
    )
    runtime = _runtime(stub)

    result = await runtime.open_app("com.apple.TextEdit")

    assert stub.calls == [("open_app", {"app": "com.apple.TextEdit"})]
    assert result.metadata["bundle_id"] == "com.apple.TextEdit"


# ---------------------------------------------------------------------------
# 3. helper error 正确向上传播
# ---------------------------------------------------------------------------


async def test_open_app_helper_error_propagates() -> None:
    stub = StubHelperClient(
        error=ComputerHelperError("app_not_found: TextEdit")
    )
    runtime = _runtime(stub)

    with pytest.raises(ComputerHelperError, match="app_not_found"):
        await runtime.open_app("TextEdit")


async def test_open_app_launch_failed_propagates() -> None:
    stub = StubHelperClient(
        error=ComputerHelperError("app_launch_failed: TextEdit")
    )
    runtime = _runtime(stub)

    with pytest.raises(ComputerHelperError, match="app_launch_failed"):
        await runtime.open_app("TextEdit")


# ---------------------------------------------------------------------------
# 4. 空 app 拒绝
# ---------------------------------------------------------------------------


async def test_open_app_rejects_empty_app() -> None:
    stub = StubHelperClient(result={})
    runtime = _runtime(stub)

    with pytest.raises(ValueError, match="non-empty"):
        await runtime.open_app("")
    with pytest.raises(ValueError, match="non-empty"):
        await runtime.open_app("   ")
    with pytest.raises(ValueError, match="non-empty"):
        await runtime.open_app(None)  # type: ignore[arg-type]
    # 校验失败时不应发送任何请求。
    assert stub.calls == []


# ---------------------------------------------------------------------------
# 5. 其它方法仍然 NotImplementedError
# ---------------------------------------------------------------------------


async def test_other_runtime_methods_still_not_implemented() -> None:
    runtime = _runtime(StubHelperClient(result={}))

    for coro in (
        runtime.observe(),
        runtime.click(None),  # type: ignore[arg-type]
        runtime.type("hi"),
        runtime.key("enter"),
        runtime.scroll(),
        runtime.focus_window("w1"),
    ):
        with pytest.raises(NotImplementedError, match="open_app"):
            await coro
