"""MacOSHelperClient：Python ↔ Swift 长驻 subprocess 的 JSON Lines 客户端。

协议（与 Swift helper 约定，见 native/macos-computer-helper）：

    Python → Swift:  {"id": 1, "method": "ping", "params": {}}
    Swift → Python:  {"id": 1, "result": {"ok": true}}
                     或
                     {"id": 1, "error": {"code": "...", "message": "..."}}

边界：
- 只维护一个长驻进程，每次 ``call`` 不重新启动；
- stdout 只读协议 JSON；stderr 作为日志逐行进入 logging；
- 支持多个并发 async call（一个 stdout reader task + pending map）；
- helper 意外退出时，所有 pending Future 统一 reject。

异常：
- ``ComputerHelperError``：helper 返回 error response 或请求失败；
- ``ComputerHelperProcessError``：进程未启动 / 意外退出 / 无法写入；
- ``ComputerHelperProtocolError``：helper 返回了无法关联或结构非法的响应。

本轮 V0 只提供传输层；不包含任何真实电脑控制逻辑。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

logger = logging.getLogger("oneagent.computer.helper")


class ComputerHelperError(RuntimeError):
    """Computer Helper 错误基类（helper 返回 error response 等）。"""


class ComputerHelperProcessError(ComputerHelperError):
    """helper 进程不存在 / 意外退出 / 无法写入。"""


class ComputerHelperProtocolError(ComputerHelperError):
    """helper 返回了无法关联或结构非法的响应。"""


class MacOSHelperClient:
    """通过 JSON Lines 与长驻 Swift helper 子进程通信。"""

    def __init__(
        self,
        helper_path: str | Path,
        *,
        helper_args: Sequence[str] = (),
    ) -> None:
        self.helper_path = Path(helper_path).expanduser().resolve()
        self.helper_args = tuple(helper_args)

        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """启动 helper 子进程并启动 stdout / stderr 读取任务（幂等）。"""

        if self._process is not None:
            return
        process = await asyncio.create_subprocess_exec(
            str(self.helper_path),
            *self.helper_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._process = process
        self._reader_task = asyncio.create_task(self._reader_loop())
        self._stderr_task = asyncio.create_task(self._stderr_loop())

    async def close(self) -> None:
        """关闭 stdin → helper 收到 EOF 退出；清理所有 pending（幂等）。"""

        if self._process is None and self._reader_task is None:
            return

        process = self._process
        if process is not None and process.stdin is not None:
            with contextlib.suppress(Exception):
                process.stdin.close()

        for task in (self._reader_task, self._stderr_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._reader_task = None
        self._stderr_task = None

        self._reject_all_pending(
            ComputerHelperProcessError("computer helper closed")
        )

        if process is not None:
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                with contextlib.suppress(Exception):
                    process.terminate()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(process.wait(), timeout=5)
        self._process = None

    # ------------------------------------------------------------------
    # 请求
    # ------------------------------------------------------------------

    async def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """发送一个请求并等待匹配响应，返回 ``result`` 对象。

        - helper 返回 ``error`` 时抛 ``ComputerHelperError``；
        - 超时 / 进程退出时抛对应异常。
        """

        process = self._process
        if process is None or process.stdin is None:
            raise ComputerHelperProcessError("computer helper is not started")

        self._next_id += 1
        request_id = self._next_id
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[request_id] = future

        line = (
            json.dumps(
                {
                    "id": request_id,
                    "method": method,
                    "params": params if params is not None else {},
                },
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")

        try:
            process.stdin.write(line)
            await process.stdin.drain()
        except Exception as exc:
            self._pending.pop(request_id, None)
            future.cancel()
            raise ComputerHelperProcessError(
                f"failed to write to computer helper: {exc}"
            ) from exc

        try:
            if timeout is not None:
                return await asyncio.wait_for(future, timeout)
            return await future
        except TimeoutError:
            self._pending.pop(request_id, None)
            future.cancel()
            raise ComputerHelperError(
                f"computer helper request {request_id} timed out"
            ) from None

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------

    async def _reader_loop(self) -> None:
        """读取 stdout 协议行并分发到对应 Future。"""

        process = self._process
        assert process is not None and process.stdout is not None
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            raw = line.strip()
            if not raw:
                continue
            try:
                self._dispatch_line(raw)
            except Exception:
                logger.exception("computer helper: failed to dispatch line %r", raw)

        # EOF → helper 进程退出；reject 所有 pending。
        self._process = None
        self._reject_all_pending(
            ComputerHelperProcessError("computer helper process exited")
        )

    async def _stderr_loop(self) -> None:
        """把 helper 的 stderr 逐行写入日志（stdout 只属于协议）。"""

        process = self._process
        assert process is not None and process.stderr is not None
        while True:
            line = await process.stderr.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip("\n")
            if text:
                logger.debug("computer helper[stderr]: %s", text)

    def _dispatch_line(self, raw: bytes) -> None:
        """解析一行 stdout 响应并完成对应 Future。"""

        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("computer helper: non-JSON stdout line dropped: %r", raw)
            return

        if not isinstance(payload, dict):
            logger.warning(
                "computer helper: non-object stdout line dropped: %r", payload
            )
            return

        msg_id = payload.get("id")
        if not isinstance(msg_id, int) or isinstance(msg_id, bool):
            logger.warning(
                "computer helper: response without valid id dropped: %r", payload
            )
            return

        future = self._pending.pop(msg_id, None)
        if future is None:
            logger.warning(
                "computer helper: response for unknown id %r dropped", msg_id
            )
            return

        if "error" in payload:
            error = payload["error"]
            if isinstance(error, dict):
                code = error.get("code")
                message = error.get("message")
            else:
                code, message = None, str(error)
            detail = f"{code}: {message}" if code is not None else str(message)
            future.set_exception(
                ComputerHelperError(
                    f"computer helper error: {detail}"
                )
            )
            return

        result = payload.get("result")
        if result is None or not isinstance(result, dict):
            future.set_exception(
                ComputerHelperProtocolError(
                    f"malformed response for id {msg_id}: {payload}"
                )
            )
            return

        future.set_result(result)

    def _reject_all_pending(self, exc: Exception) -> None:
        """把所有未完成的 pending Future 用同一异常 reject。"""

        pending = self._pending
        self._pending = {}
        for future in pending.values():
            if not future.done():
                future.set_exception(exc)


__all__ = [
    "ComputerHelperError",
    "ComputerHelperProcessError",
    "ComputerHelperProtocolError",
    "MacOSHelperClient",
]
