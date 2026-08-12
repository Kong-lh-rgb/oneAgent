"""长期记忆相关的 System Prompt 注入文本。

- ``CORE_MEMORY_HEADER`` / ``MEMORY_INDEX_HEADER``：注入段标题；
- ``MEMORY_POLICY_PROMPT``：模型使用长期记忆的规则（写入/读取策略）。
"""

from __future__ import annotations

CORE_MEMORY_HEADER = "# Core Memory"
MEMORY_INDEX_HEADER = "# Long-term Memory Index"

MEMORY_POLICY_PROMPT = """# Memory Policy

You have access to persistent long-term memory.

Long-term memory is intentionally sparse.

Do not assume all historical information is already present
in the current context.

When a memory cue appears relevant to the current task,
you may call memory_read to inspect the full memory.

Do not read memories unnecessarily.

Ordinary long-term memory consolidation happens after the run. Do not spend the
main task loop deciding whether to create, update, or archive ordinary memory.

Use core_memory_update only for the current user's explicit statement of a
stable identity, global long-term preference, or cross-task durable constraint.
Copy the supporting words exactly into explicit_user_statement. Project-specific
background and historical decisions belong to ordinary memory. Never update
Core Memory from your own inference, assistant text, tool output, or an older
message.

Use core_memory_remove only when the current user explicitly revokes an existing
Core entry, and copy that revocation exactly into explicit_user_statement.

Current task state belongs to Task, not Memory.

Reusable procedures belong to Skills, not Memory.

When unsure whether something belongs to Core Memory, do not mutate Core."""

MEMORY_WRITE_POLICY = """Create a long-term memory only when ALL of the following hold:

1. The information is likely to remain valuable across future sessions;
2. Forgetting it could cause a future violation of a durable user requirement;
3. It is not transient state of the current task;
4. It is not raw tool output;
5. It is not a one-off fact;
6. It is not procedural knowledge that belongs to Skills;
7. It is not already present in long-term memory;
8. It is not a strong inference the user has not confirmed.

When in doubt, do not create a memory."""


__all__ = [
    "CORE_MEMORY_HEADER",
    "MEMORY_INDEX_HEADER",
    "MEMORY_POLICY_PROMPT",
    "MEMORY_WRITE_POLICY",
]
