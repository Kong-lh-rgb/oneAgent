"""把未恢复的中断 Checkpoint 渲染为临时模型上下文。"""

from __future__ import annotations

import json

from app.models.types import Message, MessageRole

from .models import RunCheckpoint

CHECKPOINT_CONTEXT_MESSAGE_NAME = "vesta_interrupted_run"
_MAX_ARGUMENT_CHARS = 4_000


def render_checkpoint_context(checkpoint: RunCheckpoint) -> Message:
    """只提供恢复所需证据，不把 Checkpoint 写进原始聊天历史。"""

    payload = {
        "run_id": checkpoint.run_id,
        "original_user_message": checkpoint.user_message.content,
        "phase": checkpoint.phase.value,
        "step": checkpoint.step,
        "pending_tool_calls": [
            {
                "id": call.id,
                "name": call.name,
                "arguments": _compact_arguments(call.arguments),
                "recovery_semantics": (
                    "execution outcome is uncertain; verify before retry"
                ),
            }
            for call in checkpoint.pending_tool_calls
        ],
        "completed_tool_results": [
            {
                "tool_call_id": result.tool_call_id,
                "tool_name": result.tool_name,
                "success": result.success,
                "error": result.error,
            }
            for result in checkpoint.completed_tool_results
        ],
        "error": checkpoint.error,
        "updated_at": checkpoint.updated_at.isoformat(),
    }
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return Message(
        role=MessageRole.SYSTEM,
        name=CHECKPOINT_CONTEXT_MESSAGE_NAME,
        content=(
            "检测到当前会话上一次 Run 在终态前中断。以下内容是恢复证据，不表示"
            "未决工具一定失败：先核对 Trace 和实际环境，再决定补记完成、继续执行"
            "或询问用户。具有副作用的未决工具禁止直接重试。\n"
            f"<interrupted_run>{serialized}</interrupted_run>"
        ),
    )


def _compact_arguments(arguments: object) -> object:
    """限制恢复提示中的参数体积，完整参数仍保留在 Checkpoint。"""

    serialized = json.dumps(
        arguments,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    if len(serialized) <= _MAX_ARGUMENT_CHARS:
        return arguments
    return serialized[:_MAX_ARGUMENT_CHARS] + "…<truncated>"


__all__ = ["CHECKPOINT_CONTEXT_MESSAGE_NAME", "render_checkpoint_context"]
