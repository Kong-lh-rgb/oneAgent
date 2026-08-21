"""真实 macOS ComputerRuntime：通过 Swift helper 调用系统原生能力。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from . import errors as computer_errors
from .helper_client import (
    ComputerHelperError,
    ComputerHelperProcessError,
    ComputerHelperProtocolError,
    MacOSHelperClient,
)
from .models import (
    ActionName,
    ActionResult,
    ActiveApp,
    Bounds,
    CoordinateTarget,
    DeliveryStatus,
    Element,
    ElementStats,
    ElementTarget,
    Observation,
    VerificationStatus,
    Window,
)
from .session import (
    ComputerSession,
    ComputerSessionManager,
)

__all__ = ["MacOSComputerRuntime"]
logger = logging.getLogger("vesta.computer.macos")
_TEXT_ENTRY_ROLES = frozenset({"text_area", "text_field", "combo_box"})

# 结构化错误码 → 模型友好的 recovery hint。
_RECOVERY_HINTS = {
    computer_errors.FOREGROUND_ACTIVATION_FAILED: (
        "应用已经启动但没有成为前台；继续 observe 已绑定的 target，执行副作用前"
        "Runtime 会再次尝试激活"
    ),
    "editable_target_required": (
        "重新调用 computer_observe，并把返回的 editable element_ref 传给 computer_type"
    ),
    computer_errors.ELEMENT_NOT_EDITABLE: (
        "重新观察并选择 editable=true 的 element_ref"
    ),
    computer_errors.STALE_SNAPSHOT: (
        "目标或窗口已经变化；重新调用 computer_observe 获取新的 observation_id"
    ),
    computer_errors.ACTION_NOT_SUPPORTED: (
        "重新观察目标并选择 actions 中包含所需动作的元素"
    ),
    computer_errors.TARGET_NOT_RUNNING: (
        "目标应用已经退出；先重新调用 computer_open_app"
    ),
    computer_errors.SESSION_NOT_ACTIVE: (
        "computer_* 必须在一次活跃 Run 内执行；请重新发起一次 Run"
    ),
    computer_errors.SESSION_MISMATCH: (
        "请求的 session 已过期；Run 结束后请重新发起"
    ),
}


def _with_recovery_hint(code: str | None, message: str) -> str:
    hint = _RECOVERY_HINTS.get(code or "")
    if hint:
        return f"{message}; recovery: {hint}"
    return message


def _bounds(data: object) -> Bounds:
    raw = data if isinstance(data, dict) else {}
    return Bounds(
        x=raw.get("x", 0),
        y=raw.get("y", 0),
        width=raw.get("width", 0),
        height=raw.get("height", 0),
    )


def _window(data: dict) -> Window:
    return Window(
        ref=data.get("ref") or "w1",
        title=data.get("title") or "",
        bounds=_bounds(data.get("bounds")),
    )


def _element(data: dict) -> Element:
    raw_bounds = data.get("bounds")
    return Element(
        ref=data.get("ref") or "",
        role=data.get("role") or "",
        title=data.get("title"),
        value=data.get("value"),
        enabled=data.get("enabled", True),
        focused=data.get("focused", False),
        editable=data.get("editable", False),
        bounds=_bounds(raw_bounds) if isinstance(raw_bounds, dict) else None,
        actions=tuple(data.get("actions") or ()),
    )


def _active_app(data: object) -> ActiveApp | None:
    if not isinstance(data, dict):
        return None
    return ActiveApp(
        name=data.get("name") or "unknown",
        bundle_id=data.get("bundle_id"),
        pid=data.get("process_id"),
    )


class MacOSComputerRuntime:
    """把 ComputerRuntime 契约委托给长驻 Swift helper。"""

    def __init__(
        self,
        helper_client: MacOSHelperClient,
        screenshot_dir: Path | None = None,
        session_manager: ComputerSessionManager | None = None,
    ) -> None:
        self.helper_client = helper_client
        self.screenshot_dir = (
            (
                screenshot_dir
                or Path(__file__).resolve().parents[2]
                / ".vesta"
                / "computer"
                / "screenshots"
            )
            .expanduser()
            .resolve()
        )
        self._session_manager = session_manager or ComputerSessionManager()
        self._last_observation_id: str | None = None
        self._last_observation: Observation | None = None

    # ------------------------------------------------------------------
    # Session 生命周期
    # ------------------------------------------------------------------

    def set_session_manager(self, manager: ComputerSessionManager) -> None:
        """由 composition root 注入共享 SessionManager（与 LeaseHook 共用）。"""

        self._session_manager = manager

    def begin_session(self, run_id: str) -> ComputerSession:
        return self._session_manager.begin(run_id)

    async def end_session(self, run_id: str) -> bool:
        """结束 Run 的 Session：清 Python 状态，并通知 helper 清 Native 状态。

        幂等；helper 不可用时不阻断 Run 终态。
        """

        session = self._session_manager.get(run_id)
        ended = self._session_manager.end(run_id)
        if session is not None:
            try:
                await self.helper_client.ensure_started()
                await self.helper_client.call(
                    "end_session",
                    {"session_id": session.session_id},
                )
            except Exception:  # noqa: BLE001 - 清理失败不影响 Run 终态
                logger.exception(
                    "failed to end computer session on helper (run %s)", run_id
                )
        return ended

    def _require_session(self) -> ComputerSession:
        return self._session_manager.require_active()

    def _session_params(self, session: ComputerSession) -> dict[str, object]:
        return {"session_id": session.session_id}

    def _invalidate(self) -> None:
        self._last_observation_id = None
        self._last_observation = None

    def _require_fresh(self) -> str:
        if self._last_observation_id is None:
            raise ValueError("fresh observation required before computer action")
        return self._last_observation_id

    async def _mutation_call(
        self,
        method: str,
        params: dict[str, object],
        *,
        session: ComputerSession | None = None,
    ) -> dict:
        """副作用请求绝不重放；结构化错误码规范化，不确定/stale 时丢弃本地观察。"""

        session = session or self._require_session()
        payload = self._session_params(session)
        payload.update(params)
        try:
            return await self.helper_client.call(method, payload)
        except asyncio.CancelledError:
            self._invalidate()
            raise
        except ComputerHelperError as exc:
            raw_code = getattr(exc, "code", None)
            code = computer_errors.canonicalize(raw_code)
            uncertain = isinstance(
                exc, (ComputerHelperProcessError, ComputerHelperProtocolError)
            ) or "timed out" in str(exc)
            stale = code == computer_errors.STALE_SNAPSHOT or (
                "stale_observation" in str(exc)
            )
            if uncertain or stale:
                self._invalidate()
            if type(exc) is ComputerHelperError:
                raise ComputerHelperError(
                    _with_recovery_hint(code, str(exc))
                ) from exc
            raise

    async def start(self) -> None:
        await self.helper_client.start()

    async def close(self) -> None:
        await self.helper_client.close()

    async def open_app(self, app: str) -> ActionResult:
        if not isinstance(app, str) or not app.strip():
            raise ValueError("'app' must be a non-empty string")
        session = self._require_session()
        result = await self._mutation_call("open_app", {"app": app}, session=session)
        # open_app 把 Session Target 显式绑定到启动的应用（best effort 前台）。
        # helper 返回 {app, bundle_id, process_id}（无 name 字段），此处归一化。
        target = _active_app(
            {
                "name": result.get("app"),
                "bundle_id": result.get("bundle_id"),
                "process_id": result.get("process_id"),
            }
        )
        if target is not None:
            session.begin_target(target)
        self._invalidate()
        return ActionResult(
            success=True,
            action=ActionName.OPEN_APP,
            method=result.get("method") or "activate",
            execution_mode=result.get("execution_mode") or "foreground_fallback",
            metadata={
                "app": result.get("app"),
                "bundle_id": result.get("bundle_id"),
                "process_id": result.get("process_id"),
                "launch_status": result.get("launch_status", "running"),
                "activation_status": result.get(
                    "activation_status", "not_frontmost"
                ),
                "frontmost_verified": result.get("frontmost_verified", False),
            },
        )

    async def observe(self, include_screenshot: bool = True) -> Observation:
        if not isinstance(include_screenshot, bool):
            raise ValueError("'include_screenshot' must be a boolean")
        session = self._require_session()
        observation_id = uuid.uuid4().hex
        await self.helper_client.ensure_started()
        params: dict[str, object] = self._session_params(session)
        params.update(
            {
                "observation_id": observation_id,
                "include_screenshot": include_screenshot,
            }
        )
        if include_screenshot:
            self.screenshot_dir.mkdir(parents=True, exist_ok=True)
            params["screenshot_path"] = str(
                self.screenshot_dir / f"{observation_id}.png"
            )
        result = await self.helper_client.call("observe", params)
        target = _active_app(result.get("target"))
        user_frontmost = _active_app(result.get("user_frontmost_app"))
        if target is None:
            target = _active_app(result.get("active_app"))
        if user_frontmost is None:
            user_frontmost = _active_app(result.get("active_app"))
        windows = tuple(
            _window(item)
            for item in (result.get("windows") or [])
            if isinstance(item, dict)
        )
        if not windows and isinstance(result.get("active_window"), dict):
            raw = dict(result["active_window"])
            raw.setdefault("ref", "w1")
            windows = (_window(raw),)
        active_ref = result.get("active_window_ref")
        active_window = next(
            (item for item in windows if item.ref == active_ref),
            windows[0] if windows else None,
        )
        elements = tuple(
            _element(item)
            for item in (result.get("elements") or [])
            if isinstance(item, dict)
        )
        screenshot_ref = result.get("screenshot_ref")
        if not isinstance(screenshot_ref, str):
            screenshot_ref = None
        if result.get("screenshot_error"):
            logger.warning(
                "macOS screenshot unavailable: %s", result["screenshot_error"]
            )
        observation = Observation(
            id=observation_id,
            active_app=target,
            target=target,
            target_is_frontmost=bool(result.get("target_is_frontmost", False)),
            user_frontmost_app=user_frontmost,
            active_window=active_window,
            windows=windows,
            elements=elements,
            focused_element_ref=(
                result.get("focused_element_ref")
                if isinstance(result.get("focused_element_ref"), str)
                else None
            ),
            truncated=bool(result.get("truncated", False)),
            element_stats=ElementStats.model_validate(
                result.get("element_stats") or {}
            ),
            screenshot_ref=screenshot_ref,
        )
        # Target-bound：写回 Session（初始 observe 以当前 frontmost 为 target 时
        # 也显式落库，之后不随 frontmost 漂移）。
        if target is not None:
            session.begin_target(target)
        session.previous_user_focus = user_frontmost
        session.attach_snapshot(observation)
        self._last_observation_id = observation_id
        self._last_observation = observation
        return observation

    async def click(self, target: ElementTarget | CoordinateTarget) -> ActionResult:
        if isinstance(target, ElementTarget):
            result = await self._mutation_call(
                "click_element",
                {
                    "observation_id": target.observation_id,
                    "element_ref": target.element_ref,
                },
            )
            metadata = {
                "element_ref": target.element_ref,
                "action": result.get("action"),
            }
            method = result.get("method") or "ax_press"
        elif isinstance(target, CoordinateTarget):
            result = await self._mutation_call(
                "click_coordinate",
                {
                    "observation_id": target.observation_id,
                    "x": target.x,
                    "y": target.y,
                },
            )
            metadata = {
                "x": result.get("x", target.x),
                "y": result.get("y", target.y),
            }
            method = result.get("method") or "coordinate"
        else:
            raise ValueError("unsupported click target")
        self._invalidate()
        return ActionResult(
            success=True,
            action=ActionName.CLICK,
            observation_id=target.observation_id,
            method=method,
            execution_mode=result.get("execution_mode") or (
                "background" if method != "coordinate" else "foreground_fallback"
            ),
            metadata=metadata,
        )

    async def type(
        self,
        text: str,
        element_ref: str | None = None,
    ) -> ActionResult:
        if not isinstance(text, str):
            raise ValueError("'text' must be a string")
        observation_id = self._require_fresh()
        observation = self._last_observation
        if observation is None:  # pragma: no cover - 由 _require_fresh 保证
            raise ValueError("fresh observation required before computer action")
        target_ref = self._resolve_editable_target(observation, element_ref)
        params: dict[str, object] = {
            "text": text,
            "expected_observation_id": observation_id,
            "element_ref": target_ref,
        }
        result = await self._mutation_call("type_text", params)
        if text:
            self._invalidate()
        verification_status = VerificationStatus(
            result.get("verification_status", VerificationStatus.UNVERIFIED)
        )
        return ActionResult(
            success=verification_status is not VerificationStatus.MISMATCH,
            action=ActionName.TYPE,
            observation_id=observation_id,
            delivery_status=DeliveryStatus(
                result.get("delivery_status", DeliveryStatus.DELIVERED)
            ),
            verification_status=verification_status,
            method=result.get("method") or "cg_event_pid",
            execution_mode=result.get("execution_mode") or "background",
            metadata={
                "characters": result.get("characters", 0),
                "element_ref": result.get("element_ref", target_ref),
                "evidence": result.get("evidence", {}),
            },
        )

    @staticmethod
    def _resolve_editable_target(
        observation: Observation,
        element_ref: str | None,
    ) -> str:
        """把输入绑定到明确的可编辑元素，拒绝不确定的全局输入。"""

        by_ref = {element.ref: element for element in observation.elements}
        if element_ref is not None:
            if not isinstance(element_ref, str) or not element_ref.strip():
                raise ValueError("'element_ref' must be a non-empty string")
            normalized = element_ref.strip()
            target = by_ref.get(normalized)
            if target is None:
                raise ValueError(
                    "element_ref does not belong to the latest observation; "
                    "recovery: call computer_observe again"
                )
            if not target.editable or target.role not in _TEXT_ENTRY_ROLES:
                raise ValueError(
                    "element_not_editable: element_ref must refer to a text-entry "
                    "element (text_area/text_field/combo_box) with editable=true; "
                    "recovery: choose a text-entry role from computer_observe"
                )
            return normalized

        focused = [
            element for element in observation.elements
            if (
                element.focused
                and element.editable
                and element.role in _TEXT_ENTRY_ROLES
            )
        ]
        if len(focused) == 1:
            return focused[0].ref
        raise ValueError(
            "editable_target_required: provide element_ref from the latest "
            "observation; "
            "recovery: call computer_observe and choose editable=true"
        )

    async def key(
        self,
        key: str,
        modifiers: tuple[str, ...] = (),
        element_ref: str | None = None,
    ) -> ActionResult:
        if not isinstance(key, str) or not key.strip():
            raise ValueError("'key' must be a non-empty string")
        if not isinstance(modifiers, tuple) or not all(
            isinstance(item, str) for item in modifiers
        ):
            raise ValueError("'modifiers' must be a tuple of strings")
        observation_id = self._require_fresh()
        params: dict[str, object] = {
            "key": key,
            "modifiers": list(modifiers),
            "expected_observation_id": observation_id,
        }
        if element_ref is not None:
            if not isinstance(element_ref, str) or not element_ref.strip():
                raise ValueError("'element_ref' must be a non-empty string")
            params["element_ref"] = element_ref.strip()
        result = await self._mutation_call("key_press", params)
        self._invalidate()
        return ActionResult(
            success=True,
            action=ActionName.KEY,
            method=result.get("method") or "cg_event_pid",
            execution_mode=result.get("execution_mode") or "background",
            metadata={
                "key": result.get("key"),
                "modifiers": result.get("modifiers", []),
            },
        )

    async def scroll(self, delta_x: int = 0, delta_y: int = 0) -> ActionResult:
        for name, value in (("delta_x", delta_x), ("delta_y", delta_y)):
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"'{name}' must be an integer")
        if delta_x == 0 and delta_y == 0:
            raise ValueError("at least one scroll delta must be non-zero")
        observation_id = self._require_fresh()
        result = await self._mutation_call(
            "scroll",
            {
                "delta_x": delta_x,
                "delta_y": delta_y,
                "expected_observation_id": observation_id,
            },
        )
        self._invalidate()
        return ActionResult(
            success=True,
            action=ActionName.SCROLL,
            method=result.get("method") or "cg_event_pid",
            execution_mode=result.get("execution_mode") or "background",
            metadata={
                "delta_x": result.get("delta_x", delta_x),
                "delta_y": result.get("delta_y", delta_y),
            },
        )

    async def focus_window(self, window_ref: str) -> ActionResult:
        if not isinstance(window_ref, str) or not window_ref.strip():
            raise ValueError("'window_ref' must be a non-empty string")
        observation_id = self._require_fresh()
        result = await self._mutation_call(
            "focus_window", {"observation_id": observation_id, "window_ref": window_ref}
        )
        self._invalidate()
        return ActionResult(
            success=True,
            action=ActionName.FOCUS_WINDOW,
            observation_id=observation_id,
            method=result.get("method") or "ax_raise",
            execution_mode=result.get("execution_mode") or "foreground_fallback",
            metadata={"window_ref": result.get("window_ref", window_ref)},
        )
