"""真实 macOS ComputerRuntime：通过 Swift helper 调用系统原生能力。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

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
    ElementTarget,
    Observation,
    VerificationStatus,
    Window,
)

__all__ = ["MacOSComputerRuntime"]
logger = logging.getLogger("vesta.computer.macos")


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


class MacOSComputerRuntime:
    """把 ComputerRuntime 契约委托给长驻 Swift helper。"""

    def __init__(
        self, helper_client: MacOSHelperClient, screenshot_dir: Path | None = None
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
        self._last_observation_id: str | None = None
        self._last_observation: Observation | None = None

    def _invalidate(self) -> None:
        self._last_observation_id = None
        self._last_observation = None

    def _require_fresh(self) -> str:
        if self._last_observation_id is None:
            raise ValueError("fresh observation required before computer action")
        return self._last_observation_id

    async def _mutation_call(self, method: str, params: dict[str, object]) -> dict:
        """副作用请求绝不重放；不确定或 stale 时丢弃本地 Observation。"""

        try:
            return await self.helper_client.call(method, params)
        except asyncio.CancelledError:
            self._invalidate()
            raise
        except ComputerHelperError as exc:
            uncertain = isinstance(
                exc, (ComputerHelperProcessError, ComputerHelperProtocolError)
            ) or "timed out" in str(exc)
            stale = "stale_observation" in str(exc)
            if uncertain or stale:
                self._invalidate()
            raise

    async def start(self) -> None:
        await self.helper_client.start()

    async def close(self) -> None:
        await self.helper_client.close()

    async def open_app(self, app: str) -> ActionResult:
        if not isinstance(app, str) or not app.strip():
            raise ValueError("'app' must be a non-empty string")
        result = await self._mutation_call("open_app", {"app": app})
        self._invalidate()
        return ActionResult(
            success=True,
            action=ActionName.OPEN_APP,
            metadata={
                "app": result.get("app"),
                "bundle_id": result.get("bundle_id"),
                "process_id": result.get("process_id"),
                "frontmost_verified": result.get("frontmost_verified", False),
            },
        )

    async def observe(self, include_screenshot: bool = True) -> Observation:
        if not isinstance(include_screenshot, bool):
            raise ValueError("'include_screenshot' must be a boolean")
        observation_id = uuid.uuid4().hex
        await self.helper_client.ensure_started()
        params: dict[str, object] = {
            "observation_id": observation_id,
            "include_screenshot": include_screenshot,
        }
        if include_screenshot:
            self.screenshot_dir.mkdir(parents=True, exist_ok=True)
            params["screenshot_path"] = str(
                self.screenshot_dir / f"{observation_id}.png"
            )
        result = await self.helper_client.call("observe", params)
        app_data = result.get("active_app")
        active_app = None
        if isinstance(app_data, dict):
            active_app = ActiveApp(
                name=app_data.get("name") or "unknown",
                bundle_id=app_data.get("bundle_id"),
                pid=app_data.get("process_id"),
            )
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
            active_app=active_app,
            active_window=active_window,
            windows=windows,
            elements=elements,
            focused_element_ref=(
                result.get("focused_element_ref")
                if isinstance(result.get("focused_element_ref"), str)
                else None
            ),
            truncated=bool(result.get("truncated", False)),
            screenshot_ref=screenshot_ref,
        )
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
                "method": "ax_press",
                "action": result.get("action"),
            }
        elif isinstance(target, CoordinateTarget):
            result = await self._mutation_call(
                "click_coordinate",
                {"observation_id": target.observation_id, "x": target.x, "y": target.y},
            )
            metadata = {
                "method": "coordinate",
                "x": result.get("x", target.x),
                "y": result.get("y", target.y),
            }
        else:
            raise ValueError("unsupported click target")
        self._invalidate()
        return ActionResult(
            success=True,
            action=ActionName.CLICK,
            observation_id=target.observation_id,
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
                    "element_ref does not belong to the latest observation"
                )
            if not target.editable:
                raise ValueError("element_ref must refer to an editable element")
            return normalized

        focused = [
            element for element in observation.elements
            if element.focused and element.editable
        ]
        if len(focused) == 1:
            return focused[0].ref
        raise ValueError(
            "editable_target_required: provide element_ref from the latest observation"
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
            metadata={"window_ref": result.get("window_ref", window_ref)},
        )
