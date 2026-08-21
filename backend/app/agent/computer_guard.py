"""Computer 连续失败与无进展保护。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from app.models.types import ToolCall, ToolResult

_COMPUTER_PREFIX = "computer_"
_FAILURE_CODES = (
    "app_activation_failed",
    "editable_target_required",
    "element_not_editable",
    "stale_observation",
    "action_not_supported",
    "target_not_running",
    "element_not_found",
    "focus_failed",
    "input_effect_mismatch",
)


@dataclass(frozen=True, slots=True)
class ComputerGuardDecision:
    """一次 Computer 工具结果对循环控制的影响。"""

    feedback: str | None = None
    halt: bool = False


class ComputerStagnationGuard:
    """识别“同一目标 + 同一失败 + 桌面没有进展”的跨调用停滞。"""

    def __init__(self, *, corrective_after: int = 2, halt_after: int = 3) -> None:
        if corrective_after < 2 or halt_after <= corrective_after:
            raise ValueError("computer guard thresholds are invalid")
        self._corrective_after = corrective_after
        self._halt_after = halt_after
        self._target = "unknown"
        self._desktop_revision = 0
        self._last_observation_fingerprint: str | None = None
        self._failures: dict[tuple[str, str, int], int] = {}

    def record(
        self,
        tool_call: ToolCall,
        result: ToolResult,
    ) -> ComputerGuardDecision:
        """记录结果；非 Computer 工具不参与判断。"""

        if not tool_call.name.startswith(_COMPUTER_PREFIX):
            return ComputerGuardDecision()

        payload = _json_object(result.output)
        self._update_target(tool_call.name, payload)
        if result.success:
            self._record_progress(tool_call.name, payload)
            return ComputerGuardDecision()

        failure_code = _failure_code(result.error)
        key = (self._target, failure_code, self._desktop_revision)
        count = self._failures.get(key, 0) + 1
        self._failures[key] = count
        if count >= self._halt_after:
            return ComputerGuardDecision(
                feedback=(
                    "Computer attempts halted: the same target failed with "
                    f"{failure_code} {count} times without observable desktop "
                    "progress. Stop retrying computer tools and explain the blocker."
                ),
                halt=True,
            )
        if count >= self._corrective_after:
            return ComputerGuardDecision(
                feedback=(
                    "Repeated computer failure without desktop progress: "
                    f"{failure_code}. Change strategy using the recovery guidance; "
                    "do not repeat the same desktop attempt."
                )
            )
        return ComputerGuardDecision()

    def _update_target(self, tool_name: str, payload: dict[str, object]) -> None:
        if tool_name == "computer_open_app":
            metadata = payload.get("metadata")
            if isinstance(metadata, dict):
                identity = metadata.get("process_id") or metadata.get("bundle_id")
                if identity is not None:
                    self._set_target(str(identity))
            return
        if tool_name == "computer_observe":
            target = payload.get("target") or payload.get("active_app")
            if isinstance(target, dict):
                identity = (
                    target.get("pid")
                    or target.get("process_id")
                    or target.get("bundle_id")
                    or target.get("name")
                )
                if identity is not None:
                    self._set_target(str(identity))

    def _set_target(self, identity: str) -> None:
        if identity != self._target:
            self._target = identity
            self._desktop_revision += 1
            self._last_observation_fingerprint = None

    def _record_progress(
        self,
        tool_name: str,
        payload: dict[str, object],
    ) -> None:
        if tool_name == "computer_observe":
            fingerprint = _observation_fingerprint(payload)
            if fingerprint != self._last_observation_fingerprint:
                self._last_observation_fingerprint = fingerprint
                self._desktop_revision += 1
            return
        if tool_name == "computer_type" and (
            payload.get("verification_status") == "verified"
        ):
            self._desktop_revision += 1


def _json_object(raw: str | None) -> dict[str, object]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _failure_code(error: str | None) -> str:
    text = error or "unknown_computer_failure"
    for code in _FAILURE_CODES:
        if code in text:
            return code
    match = re.search(r"\b([a-z][a-z0-9_]{2,})\b", text.lower())
    return match.group(1) if match else "unknown_computer_failure"


def _observation_fingerprint(payload: dict[str, object]) -> str:
    """忽略 ID、坐标和统计抖动，只比较任务相关的语义桌面证据。"""

    target = payload.get("target") or payload.get("active_app")
    target_identity: object = target
    if isinstance(target, dict):
        target_identity = {
            key: target.get(key)
            for key in ("pid", "process_id", "bundle_id", "name")
            if target.get(key) is not None
        }
    window = payload.get("active_window")
    window_identity: object = window
    if isinstance(window, dict):
        window_identity = {
            key: window.get(key)
            for key in ("ref", "title")
            if window.get(key) is not None
        }
    semantic_elements: list[object] = []
    raw_elements = payload.get("elements")
    if isinstance(raw_elements, list):
        for element in raw_elements:
            if not isinstance(element, dict):
                continue
            semantic_elements.append(
                {
                    key: element.get(key)
                    for key in (
                        "role",
                        "title",
                        "value",
                        "enabled",
                        "focused",
                        "editable",
                        "actions",
                    )
                    if element.get(key) is not None
                }
            )
    evidence = {
        "target": target_identity,
        "active_window": window_identity,
        "elements": semantic_elements,
    }
    encoded = json.dumps(
        evidence,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
