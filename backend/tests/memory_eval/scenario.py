"""多阶段长期记忆测评的数据模型。"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class InitialMemoryStatus(StrEnum):
    """场景预置普通记忆的状态。"""

    ACTIVE = "active"
    ARCHIVED = "archived"


class InitialMemory(BaseModel):
    """一条带稳定别名的预置普通记忆。"""

    model_config = ConfigDict(extra="forbid")

    alias: str
    title: str
    summary: str
    content: str
    status: InitialMemoryStatus = InitialMemoryStatus.ACTIVE
    archive_reason: str = "测评预置归档"

    @field_validator("alias")
    @classmethod
    def normalize_alias(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("memory alias cannot be empty")
        return normalized


class TextExpectation(BaseModel):
    """自然语言文本的宽松关键点断言。"""

    model_config = ConfigDict(extra="forbid")

    contains: tuple[str, ...] = ()
    excludes: tuple[str, ...] = ()


class StoredMemoryExpectation(BaseModel):
    """阶段结束后某条 Memory 文件的期望。"""

    model_config = ConfigDict(extra="forbid")

    target: str
    title_contains: tuple[str, ...] = ()
    summary_contains: tuple[str, ...] = ()
    content_contains: tuple[str, ...] = ()
    revision_at_least: int | None = Field(default=None, ge=1)


class PhaseExpectation(BaseModel):
    """单阶段的行为和最终存储断言。"""

    model_config = ConfigDict(extra="forbid")

    reflection_action: str | None = Field(
        default=None,
        pattern=r"^(none|create|update)$",
    )
    reflection_mutation_applied: bool | None = None
    maintenance_action: str | None = Field(
        default=None,
        pattern=r"^(archive|defer)$",
    )
    recalled: tuple[str, ...] = ()
    not_recalled: tuple[str, ...] = ()
    total_memory_reads: int | None = Field(default=None, ge=0)
    answer: TextExpectation = Field(default_factory=TextExpectation)
    active_count: int | None = Field(default=None, ge=0)
    archive_count: int | None = Field(default=None, ge=0)
    core_contains: tuple[str, ...] = ()
    memory: StoredMemoryExpectation | None = None


class MemoryEvalPhase(BaseModel):
    """一次 Agent Run；同 conversation 的 Phase 自动继承历史。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    conversation: str = "A"
    user_input: str
    bind_reflection_memory_as: str | None = None
    max_steps: int = Field(default=10, ge=1)
    max_tool_rounds: int | None = Field(default=3, ge=0)
    expect: PhaseExpectation = Field(default_factory=PhaseExpectation)
    expect_off: PhaseExpectation | None = None

    @field_validator("id", "conversation", "user_input")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("phase id, conversation and user_input cannot be empty")
        return normalized


class MemoryEvalScenario(BaseModel):
    """共享同一 Memory Store 的跨 Run、跨会话测评场景。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    tags: tuple[str, ...] = ()
    max_active: int = Field(default=25, gt=0)
    initial_core: str = ""
    initial_memories: tuple[InitialMemory, ...] = ()
    phases: tuple[MemoryEvalPhase, ...] = Field(min_length=1)

    @field_validator("id", "name")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("scenario id and name cannot be empty")
        return normalized

    @model_validator(mode="after")
    def validate_unique_names(self) -> MemoryEvalScenario:
        aliases = [memory.alias for memory in self.initial_memories]
        if len(aliases) != len(set(aliases)):
            raise ValueError("initial memory aliases must be unique")
        phase_ids = [phase.id for phase in self.phases]
        if len(phase_ids) != len(set(phase_ids)):
            raise ValueError("phase IDs must be unique within a scenario")
        bound = [
            phase.bind_reflection_memory_as
            for phase in self.phases
            if phase.bind_reflection_memory_as is not None
        ]
        all_aliases = aliases + bound
        if len(all_aliases) != len(set(all_aliases)):
            raise ValueError("memory aliases and phase bindings must be unique")
        return self


__all__ = [
    "InitialMemory",
    "InitialMemoryStatus",
    "MemoryEvalPhase",
    "MemoryEvalScenario",
    "PhaseExpectation",
    "StoredMemoryExpectation",
    "TextExpectation",
]
