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
you may call memory.read to inspect the full memory.

Do not read memories unnecessarily.

Create or update memory only when information has durable
cross-session value.

Current task state belongs to Task, not Memory.

Reusable procedures belong to Skills, not Memory.

When unsure whether something should become long-term memory,
do not store it.

If memory.create reports that maintenance is required, resolve it before
finishing the current run. Use memory.read when needed, then KEEP, MERGE with
memory.update plus memory.archive, or ARCHIVE until active memory is within
the capacity limit. Do not create more memories while maintenance is pending."""

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
