"""Skill Learning V1 的领域模型。

边界（与 Task / Trace / Skill 严格区分）：
- ``TaskCard`` 只是 Pattern Mining 使用的轻量投影，不是新事实源；
- ``TaskPatternCluster`` 是 Pattern Mining 的结构化输出；
- ``SkillCandidate`` 是尚未生效、需要人工 Gate 的候选过程知识；
- 正式 ``Skill`` 只允许在 Candidate 被 accept 后由 Service 生成。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import (
    BaseModel,
    ConfigDict,
    field_validator,
    model_validator,
)

_SKILL_NAME_MAX_LENGTH = 64


def _normalize_text(value: str) -> str:
    return " ".join(value.split()).strip()


# ---------------------------------------------------------------------------
# Task 投影
# ---------------------------------------------------------------------------


class TaskCard(BaseModel):
    """Completed Task 的轻量投影，供 Pattern Mining 使用。

    只包含任务最终事实（title/goal/constraints/key_facts/final steps），
    不读取完整对话或 Trace；miner 的第一阶段不接触执行证据。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    title: str
    description: str | None = None
    goal: str | None = None
    constraints: tuple[str, ...] = ()
    key_facts: tuple[str, ...] = ()
    final_steps: tuple[str, ...] = ()
    created_at: datetime
    completed_at: datetime | None = None
    run_count: int = 0

    @field_validator("created_at", "completed_at")
    @classmethod
    def normalize_datetime(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("task card datetimes must include timezone information")
        return value.astimezone(UTC)

    def render(self) -> str:
        """渲染为传给模型的紧凑 JSON 段。"""

        return self.model_dump_json()


# ---------------------------------------------------------------------------
# Pattern Mining 输出
# ---------------------------------------------------------------------------


class TaskPatternCluster(BaseModel):
    """一组本质相似的 Completed Task 形成的模式簇。

    只描述"存在什么可复用模式"，不直接生成 SKILL.md。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    task_ids: tuple[str, ...]
    pattern_name: str
    description: str
    similarity_reason: str
    reusable_value: str

    @field_validator(
        "id",
        "pattern_name",
        "description",
        "similarity_reason",
        "reusable_value",
    )
    @classmethod
    def normalize_required_text(cls, value: object) -> str:
        if not isinstance(value, str):
            raise TypeError("cluster text fields must be strings")
        normalized = _normalize_text(value)
        if not normalized:
            raise ValueError("cluster text fields cannot be empty")
        return normalized

    @field_validator("task_ids")
    @classmethod
    def normalize_task_ids(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)):
            raise TypeError("task_ids must be a list")
        seen: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise TypeError("task_ids must contain only strings")
            normalized = item.strip()
            if normalized and normalized not in seen:
                seen.append(normalized)
        if not seen:
            raise ValueError("task_ids cannot be empty")
        return tuple(seen)


class PatternMiningResult(BaseModel):
    """Pattern Mining 的完整结构输出；允许空 clusters。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    clusters: tuple[TaskPatternCluster, ...] = ()


# ---------------------------------------------------------------------------
# Skill Candidate
# ---------------------------------------------------------------------------


class SkillCandidateStatus(StrEnum):
    """Candidate 的人工 Gate 状态。"""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class SkillCandidateAction(StrEnum):
    """Candidate 应用到正式 Skill 的动作。"""

    CREATE = "create"
    UPDATE = "update"


class SkillCandidate(BaseModel):
    """从多个 Completed Task 提炼出的、尚未生效的候选过程知识。

    可追溯性要求：必须保存 source_task_ids / source_run_ids / reason /
    evidence_summary，不允许只保存一份最终 Markdown。
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    action: SkillCandidateAction
    proposed_name: str
    description: str
    reason: str
    procedure: tuple[str, ...] = ()
    pitfalls: tuple[str, ...] = ()
    verification: tuple[str, ...] = ()
    source_task_ids: tuple[str, ...] = ()
    source_run_ids: tuple[str, ...] = ()
    existing_skill_name: str | None = None
    status: SkillCandidateStatus = SkillCandidateStatus.PENDING
    created_at: datetime
    reviewed_at: datetime | None = None
    evidence_summary: str = ""

    @field_validator("id", "proposed_name", "description", "reason", mode="before")
    @classmethod
    def normalize_required_text(cls, value: object) -> str:
        if not isinstance(value, str):
            raise TypeError("candidate text fields must be strings")
        normalized = _normalize_text(value)
        if not normalized:
            raise ValueError("candidate text fields cannot be empty")
        return normalized

    @field_validator("proposed_name")
    @classmethod
    def validate_proposed_name(cls, value: str) -> str:
        from app.skills import validate_skill_name

        try:
            return validate_skill_name(value)
        except ValueError as exc:
            raise ValueError(f"proposed skill name is invalid: {exc}") from exc

    @field_validator("existing_skill_name", mode="before")
    @classmethod
    def normalize_optional_skill_name(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("existing_skill_name must be a string or None")
        normalized = _normalize_text(value)
        return normalized or None

    @field_validator("created_at", "reviewed_at")
    @classmethod
    def normalize_datetime(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("candidate datetimes must include timezone information")
        return value.astimezone(UTC)

    @field_validator(
        "procedure",
        "pitfalls",
        "verification",
        "source_task_ids",
        "source_run_ids",
        mode="before",
    )
    @classmethod
    def normalize_entries(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            raise TypeError("candidate entries must be lists")
        seen: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise TypeError("candidate entries must contain only strings")
            normalized = _normalize_text(item)
            if normalized and normalized not in seen:
                seen.append(normalized)
        return tuple(seen)

    @model_validator(mode="after")
    def validate_action(self) -> SkillCandidate:
        if self.action is SkillCandidateAction.UPDATE and not self.existing_skill_name:
            raise ValueError("update candidate requires existing_skill_name")
        if (
            self.action is SkillCandidateAction.CREATE
            and self.existing_skill_name is not None
        ):
            raise ValueError("create candidate cannot have existing_skill_name")
        if not self.source_task_ids:
            raise ValueError("candidate must reference at least one source task")
        if not self.procedure:
            raise ValueError("candidate must contain at least one procedure step")
        return self


__all__ = [
    "PatternMiningResult",
    "SkillCandidate",
    "SkillCandidateAction",
    "SkillCandidateStatus",
    "TaskCard",
    "TaskPatternCluster",
]
