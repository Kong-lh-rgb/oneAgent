"""Computer Runtime 统一结构化错误码（Python ↔ Swift 协议共用词汇表）。

错误码是稳定标识符，不通过解析一大段自然语言判断 Runtime 状态。
模型友好的 recovery hint 保留在 message 里，但判定逻辑一律使用 code。
"""

from __future__ import annotations

# --- Session / 生命周期 ---
SESSION_NOT_ACTIVE = "session_not_active"
SESSION_MISMATCH = "session_mismatch"

# --- Target ---
TARGET_NOT_SET = "target_not_set"
TARGET_NOT_RUNNING = "target_not_running"
TARGET_WINDOW_NOT_FOUND = "target_window_not_found"

# --- Snapshot / 元素 ---
STALE_SNAPSHOT = "stale_snapshot"
ELEMENT_NOT_FOUND = "element_not_found"
ELEMENT_NOT_EDITABLE = "element_not_editable"
ACTION_NOT_SUPPORTED = "action_not_supported"

# --- 动作执行 ---
BACKGROUND_ACTION_FAILED = "background_action_failed"
FOREGROUND_ACTIVATION_FAILED = "foreground_activation_failed"

# --- 其它 ---
SCREEN_CAPTURE_UNAVAILABLE = "screen_capture_unavailable"
SCREEN_RECORDING_PERMISSION_REQUIRED = "screen_recording_permission_required"
ACCESSIBILITY_PERMISSION_REQUIRED = "accessibility_permission_required"
INVALID_PARAMS = "invalid_params"

# 兼容旧协议使用的别名 → 新错误码映射（有界迁移）。
LEGACY_TO_CANONICAL: dict[str, str] = {
    "stale_observation": STALE_SNAPSHOT,
    "focus_failed": BACKGROUND_ACTION_FAILED,
    "input_event_failed": BACKGROUND_ACTION_FAILED,
    "input_effect_mismatch": BACKGROUND_ACTION_FAILED,
    "coordinate_out_of_bounds": INVALID_PARAMS,
    "screenshot_unavailable": SCREEN_CAPTURE_UNAVAILABLE,
    "app_activation_failed": FOREGROUND_ACTIVATION_FAILED,
    "app_launch_failed": BACKGROUND_ACTION_FAILED,
    "app_not_found": INVALID_PARAMS,
}


def canonicalize(code: str | None) -> str | None:
    """把协议错误码规范化到稳定词汇表；未知码原样返回。"""

    if not code:
        return None
    return LEGACY_TO_CANONICAL.get(code, code)


__all__ = [
    "ACTION_NOT_SUPPORTED",
    "ACCESSIBILITY_PERMISSION_REQUIRED",
    "BACKGROUND_ACTION_FAILED",
    "ELEMENT_NOT_EDITABLE",
    "ELEMENT_NOT_FOUND",
    "FOREGROUND_ACTIVATION_FAILED",
    "INVALID_PARAMS",
    "LEGACY_TO_CANONICAL",
    "SCREEN_CAPTURE_UNAVAILABLE",
    "SCREEN_RECORDING_PERMISSION_REQUIRED",
    "SESSION_MISMATCH",
    "SESSION_NOT_ACTIVE",
    "STALE_SNAPSHOT",
    "TARGET_NOT_RUNNING",
    "TARGET_NOT_SET",
    "TARGET_WINDOW_NOT_FOUND",
    "canonicalize",
]
