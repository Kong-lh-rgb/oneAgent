"""Skill 上下文 Provider：轻量 Catalog 注入 + Active Skill 指令块。

- Catalog 是每个 Run 的独立动态块，只含 name + description，不写入持久历史；
- Active Skill 指令在激活后每个 Agent Step 重新注入，独立于普通 ToolResult，
  因此不会被 ToolReducer / ConversationReducer / Compaction 遗忘；
- Active 指令总量受 ``skill_context_max_tokens`` 约束，超预算时按激活顺序
  确定性拒绝新 Skill。
"""

from __future__ import annotations

from collections.abc import Sequence

from app.context.tokens import default_token_estimator
from app.models.types import Message, MessageRole

from .models import Skill, SkillMetadata

SKILL_CATALOG_MESSAGE_NAME = "oneagent_skill_catalog"
ACTIVE_SKILL_MESSAGE_NAME = "oneagent_active_skill"

_CATALOG_HEADER = (
    "# Available Skills\n\n"
    "The following skills are available. When the current task matches one of "
    "them, call skill_read with that name to activate it and follow its "
    "instructions.\n"
)


class SkillContextProvider:
    """把 Skill Catalog 与 Active Skill 指令渲染成模型上下文消息。"""

    def __init__(
        self,
        *,
        max_tokens: int,
        max_active: int,
    ) -> None:
        self.max_tokens = max_tokens
        self.max_active = max_active
        self._estimator = default_token_estimator()

    # ------------------------------------------------------------------
    # Catalog（metadata only）
    # ------------------------------------------------------------------

    def render_catalog(self, metadata: Sequence[SkillMetadata]) -> str:
        if not metadata:
            return _CATALOG_HEADER + "(No skills available.)\n"
        lines = [_CATALOG_HEADER]
        for item in metadata:
            lines.append(f"[{item.name}] {item.description}")
        return "\n".join(lines).rstrip() + "\n"

    def catalog_message(
        self,
        metadata: Sequence[SkillMetadata],
    ) -> Message | None:
        text = self.render_catalog(metadata)
        return Message(
            role=MessageRole.SYSTEM,
            name=SKILL_CATALOG_MESSAGE_NAME,
            content=text,
        )

    def catalog_tokens(self, metadata: Sequence[SkillMetadata]) -> int:
        return self._estimator.estimate_text(self.render_catalog(metadata))

    # ------------------------------------------------------------------
    # Active Skill 指令
    # ------------------------------------------------------------------

    def active_messages(
        self,
        skills: Sequence[Skill],
    ) -> tuple[Message, ...]:
        """按激活顺序渲染 Active Skill 指令块（去重后）。"""

        seen: set[str] = set()
        messages: list[Message] = []
        for skill in skills:
            if skill.metadata.name in seen:
                continue
            seen.add(skill.metadata.name)
            messages.append(
                Message(
                    role=MessageRole.SYSTEM,
                    name=ACTIVE_SKILL_MESSAGE_NAME,
                    content=skill.render_instructions(),
                )
            )
        return tuple(messages)

    def active_tokens(self, skills: Sequence[Skill]) -> int:
        total = 0
        for message in self.active_messages(skills):
            total += self._estimator.estimate_text(message.content or "")
        return total

    def would_exceed_budget(
        self,
        current: Sequence[Skill],
        candidate: Skill,
    ) -> bool:
        """判断激活 candidate 后是否超过总预算（确定性：按序保留，超则拒绝）。"""

        if len(current) >= self.max_active:
            return True
        current_skills = [skill for skill in current]
        if any(
            skill.metadata.name == candidate.metadata.name for skill in current_skills
        ):
            return False
        current_skills.append(candidate)
        return self.active_tokens(current_skills) > self.max_tokens


__all__ = [
    "ACTIVE_SKILL_MESSAGE_NAME",
    "SKILL_CATALOG_MESSAGE_NAME",
    "SkillContextProvider",
]
