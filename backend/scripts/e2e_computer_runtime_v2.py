"""Computer Runtime V2 的真实 macOS TextEdit 冒烟测试。

运行方式（先完成 swift build，并授予 helper 辅助功能权限）：

    PYTHONPATH=. .venv/bin/python scripts/e2e_computer_runtime_v2.py

该脚本不走 Agent、审批或模型，只验证 Runtime 与 Native helper 的输入契约。
测试文件位于临时目录，输入完全使用 targeted CGEvent，不使用剪贴板或
AXSetValue。
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

from app.computer import MacOSComputerRuntime, MacOSHelperClient, resolve_helper_path
from app.computer.models import Element, Observation, VerificationStatus

_EDITABLE_ROLES = frozenset({"text_area", "text_field", "combo_box"})


def _editable(observation: Observation) -> Element:
    candidates = [
        element
        for element in observation.elements
        if element.editable and element.role in _EDITABLE_ROLES
    ]
    if not candidates:
        raise AssertionError("TextEdit observation 中没有可编辑元素")
    return next((item for item in candidates if item.focused), candidates[0])


async def _observe_until_text(
    runtime: MacOSComputerRuntime,
    expected: str,
    *,
    attempts: int = 12,
) -> tuple[Observation, Element]:
    """只以 mutation 后的 fresh observe 判断真实 UI 内容。"""

    latest = ""
    for _ in range(attempts):
        observation = await runtime.observe(include_screenshot=False)
        element = _editable(observation)
        latest = element.value or ""
        if latest == expected:
            return observation, element
        await asyncio.sleep(0.15)
    raise AssertionError(
        f"TextEdit 内容不匹配：expected={expected!r}, actual={latest!r}"
    )


async def _main() -> None:
    if sys.platform != "darwin":
        raise SystemExit("该 E2E 只能在 macOS 运行")
    helper_path = resolve_helper_path()
    if helper_path is None:
        raise SystemExit("未找到 Native helper；请先运行 swift build")

    client = MacOSHelperClient(helper_path)
    runtime = MacOSComputerRuntime(client)
    run_id = f"e2e-computer-v2-{uuid4().hex}"

    with tempfile.TemporaryDirectory(prefix="vesta-computer-v2-") as temp_dir:
        document = Path(temp_dir) / "append-semantics.txt"
        document.write_text("", encoding="utf-8")
        await client.start()
        try:
            await runtime.begin_session_rpc(run_id)
            opener = await asyncio.create_subprocess_exec(
                "open", "-a", "TextEdit", str(document)
            )
            if await opener.wait() != 0:
                raise RuntimeError("无法用 TextEdit 打开临时测试文件")
            await asyncio.sleep(0.8)

            await runtime.open_app("TextEdit")
            first = await runtime.observe(include_screenshot=False)
            editor = _editable(first)

            delivered = await runtime.type("hello", element_ref=editor.ref)
            assert delivered.verification_status is VerificationStatus.UNVERIFIED
            _, editor = await _observe_until_text(runtime, "hello")
            print("PASS: fresh observe 确认首次输入 hello")

            # 把插入点移动到行尾；key 同样只能使用当前 Session 的精确目标。
            await runtime.key("right", modifiers=("command",), element_ref=editor.ref)
            after_key = await runtime.observe(include_screenshot=False)
            editor = _editable(after_key)

            appended = await runtime.type(" Vesta", element_ref=editor.ref)
            assert appended.verification_status is VerificationStatus.UNVERIFIED
            _, editor = await _observe_until_text(runtime, "hello Vesta")
            assert editor.value == "hello Vesta"
            print("PASS: computer_type 保留原内容并得到 hello Vesta")

            # 关闭本次精确目标窗口，避免 E2E 在桌面留下临时文档。
            await runtime.key("w", modifiers=("command",), element_ref=editor.ref)
            await asyncio.sleep(0.3)
        finally:
            await runtime.end_session(run_id)
            await client.close()


if __name__ == "__main__":
    asyncio.run(_main())
