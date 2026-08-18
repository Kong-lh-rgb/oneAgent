"""Eval 场景的数据模型。

每条场景用 YAML 描述：预置环境（历史/Task/文件）、用户输入、Runtime 限制、
审批/上下文覆盖，以及期望（工具调用、Task 状态、文件、回答关键点、是否压缩）。

评分宽松优先：工具只检查"必须包含 / 禁止包含 / 参数关键值"，不要求完整轨迹
一模一样；Task 只检查状态与关键字段。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.agent.result import AgentStopReason
from app.task import TaskStatus, TaskStepStatus

VALID_GROUPS = (
    "basic",
    "tools",
    "task",
    "context",
    "safety",
    "skill",
    "learning",
)


class InitialMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    content: str | None = None

    @field_validator("role")
    @classmethod
    def valid_role(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ("system", "user", "assistant", "tool"):
            raise ValueError(f"invalid message role: {value}")
        return normalized


class InitialStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    status: TaskStepStatus = TaskStepStatus.TODO
    note: str | None = None


class InitialTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alias: str | None = None
    title: str
    description: str | None = None
    goal: str | None = None
    status: TaskStatus = TaskStatus.PENDING
    steps: tuple[InitialStep, ...] = ()
    owner: str | None = None
    constraints: tuple[str, ...] = ()
    key_facts: tuple[str, ...] = ()
    run_ids: tuple[str, ...] = ()


class InitialTraceEvent(BaseModel):
    """预置 Trace 中的一条 AgentEvent（Learning 场景使用）。"""

    model_config = ConfigDict(extra="forbid")

    type: str
    tool_name: str | None = None
    arguments: dict[str, object] = Field(default_factory=dict)
    success: bool = True
    error: str | None = None


class InitialTraceRun(BaseModel):
    """预置一条 Agent Run 的完整事件序列。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    events: tuple[InitialTraceEvent, ...] = ()


class InitialFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    content: str = ""


class InitialSkill(BaseModel):
    """预置到评测环境的一个 Skill（目录式：<name>/SKILL.md + 可选 references）。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    body: str = "# 步骤\n\n1. 执行"
    reference_files: tuple[InitialFile, ...] = ()

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        from app.skills import validate_skill_name

        return validate_skill_name(value)


class InitialCandidate(BaseModel):
    """预置到评测环境的一个 Pending SkillCandidate（Learning 场景）。"""

    model_config = ConfigDict(extra="forbid")

    proposed_name: str
    description: str = ""
    action: str = "create"
    existing_skill_name: str | None = None
    source_task_ids: tuple[str, ...] = ()
    reason: str = "预置待评审候选"

    @field_validator("proposed_name")
    @classmethod
    def valid_proposed_name(cls, value: str) -> str:
        from app.skills import validate_skill_name

        return validate_skill_name(value)


class ApprovalPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deny_tools: tuple[str, ...] = ()
    approve_tools: tuple[str, ...] = ()


class ContextOverrides(BaseModel):
    """用于强制触发压缩的预算覆盖（可选）。"""

    model_config = ConfigDict(extra="forbid")

    window_override: int | None = Field(default=None, gt=0)
    margin_tokens: int | None = Field(default=None, ge=0)
    keep_recent_conversation_blocks: int | None = Field(default=None, ge=0)


class ToolExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    must: tuple[str, ...] = ()
    must_not: tuple[str, ...] = ()
    successful: tuple[str, ...] = ()
    unsuccessful: tuple[str, ...] = ()
    no_successful: tuple[str, ...] = ()
    args: dict[str, dict[str, object]] = Field(default_factory=dict)
    count: dict[str, int] = Field(default_factory=dict)
    total_count: int | None = Field(default=None, ge=0)
    ordered: tuple[str, ...] = ()
    approval_denied: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_expectations(self) -> ToolExpectation:
        """拒绝相互矛盾或无法满足的工具期望。"""

        required = set(self.must) | set(self.successful) | set(self.unsuccessful)
        conflicts = required & set(self.must_not)
        if conflicts:
            raise ValueError(
                f"tools cannot be both required and forbidden: {sorted(conflicts)}"
            )
        negative_counts = [name for name, value in self.count.items() if value < 0]
        if negative_counts:
            raise ValueError(f"tool counts cannot be negative: {negative_counts}")
        return self


class StepExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    status: TaskStepStatus | None = None
    status_any: tuple[TaskStepStatus, ...] = ()
    note_required: bool = False


class TaskExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    created: bool | None = None
    new_count: int | None = Field(default=None, ge=0)
    target: str | None = None
    status_any: tuple[TaskStatus, ...] = ()
    title_contains: tuple[str, ...] = ()
    goal_contains: tuple[str, ...] = ()
    content_contains: tuple[str, ...] = ()
    min_steps: int | None = Field(default=None, ge=0)
    steps: tuple[StepExpectation, ...] = ()
    all_steps_done: bool | None = None


class FileExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    exists: bool = True
    contains: tuple[str, ...] = ()


class AnswerExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keypoints: tuple[str, ...] = ()
    any_of: tuple[str, ...] = ()


class SkillExpectation(BaseModel):
    """Skill 激活期望：应激活 / 不应激活 / 应激活失败。"""

    model_config = ConfigDict(extra="forbid")

    activated: tuple[str, ...] = ()
    not_activated: tuple[str, ...] = ()
    activation_failed: tuple[str, ...] = ()
    survives_compaction: bool = False

    @model_validator(mode="after")
    def no_conflicts(self) -> SkillExpectation:
        conflicts = (
            set(self.activated)
            & (set(self.not_activated) | set(self.activation_failed))
        )
        if conflicts:
            raise ValueError(
                f"skill cannot be both activated and forbidden/failed: "
                f"{sorted(conflicts)}"
            )
        return self


class SkillLearningExpectation(BaseModel):
    """Skill Learning 期望：候选数量、动作、名字与最终 Skill。"""

    model_config = ConfigDict(extra="forbid")

    batch_size: int = Field(default=20, ge=1)
    candidate_count: int | None = Field(default=None, ge=0)
    create_count: int | None = Field(default=None, ge=0)
    update_count: int | None = Field(default=None, ge=0)
    expected_names: tuple[str, ...] = ()
    no_candidates: bool = False
    created_skill_names: tuple[str, ...] = ()
    # 预期属于同一模式簇的 Task alias（用于 Live Eval 计算 Cluster Precision/Recall）。
    expected_pattern_task_aliases: tuple[str, ...] = ()
    # 期望被提炼进 pitfalls 的关键词（用于 Pitfall Recall 估算）。
    expected_pitfall_keywords: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_expectation(self) -> SkillLearningExpectation:
        if self.no_candidates and self.candidate_count not in (None, 0):
            raise ValueError(
                "no_candidates conflicts with a positive candidate_count"
            )
        return self


class Expectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tools: ToolExpectation = Field(default_factory=ToolExpectation)
    task: TaskExpectation = Field(default_factory=TaskExpectation)
    skill: SkillExpectation = Field(default_factory=SkillExpectation)
    learning: SkillLearningExpectation = Field(default_factory=SkillLearningExpectation)
    files: tuple[FileExpectation, ...] = ()
    answer: AnswerExpectation = Field(default_factory=AnswerExpectation)
    requires_compaction: bool = False
    stop_reason_any: tuple[AgentStopReason, ...] = Field(
        default=(AgentStopReason.FINAL_ANSWER,),
        min_length=1,
    )


class Scenario(BaseModel):
    """一条完整测评场景。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    group: str = "basic"
    name: str
    user_input: str
    initial_history: tuple[InitialMessage, ...] = ()
    initial_tasks: tuple[InitialTask, ...] = ()
    initial_files: tuple[InitialFile, ...] = ()
    initial_skills: tuple[InitialSkill, ...] = ()
    initial_runs: tuple[InitialTraceRun, ...] = ()
    initial_pending_candidates: tuple[InitialCandidate, ...] = ()
    allowed_tools: tuple[str, ...] | None = None
    max_steps: int = 10
    max_tool_rounds: int | None = None
    max_output_tokens: int | None = None
    approval: ApprovalPolicy = Field(default_factory=ApprovalPolicy)
    context: ContextOverrides = Field(default_factory=ContextOverrides)
    expect: Expectation

    @field_validator("id")
    @classmethod
    def id_normalized(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("scenario id cannot be empty")
        return normalized

    @field_validator("group")
    @classmethod
    def group_valid(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in VALID_GROUPS:
            raise ValueError(
                f"scenario group must be one of {VALID_GROUPS}: {value}"
            )
        return normalized

    @field_validator("max_steps")
    @classmethod
    def max_steps_valid(cls, value: int) -> int:
        if value < 1:
            raise ValueError("max_steps must be at least 1")
        return value

    @model_validator(mode="after")
    def validate_cross_field_expectations(self) -> Scenario:
        """在花费模型调用前发现场景中的拼写和语义冲突。"""

        aliases = [task.alias for task in self.initial_tasks if task.alias]
        if len(aliases) != len(set(aliases)):
            raise ValueError("initial task aliases must be unique")

        task_expect = self.expect.task
        if task_expect.created is False and task_expect.new_count not in (None, 0):
            raise ValueError("created=false conflicts with a positive new_count")
        if task_expect.created is True and task_expect.new_count == 0:
            raise ValueError("created=true conflicts with new_count=0")
        if (
            task_expect.target not in (None, "new")
            and task_expect.target not in aliases
        ):
            raise ValueError(
                f"unknown task target alias: {task_expect.target}"
            )
        if task_expect.target == "new" and task_expect.created is False:
            raise ValueError("target=new conflicts with created=false")

        allowed = set(self.allowed_tools) if self.allowed_tools is not None else None
        if allowed is not None:
            required = (
                set(self.expect.tools.must)
                | set(self.expect.tools.successful)
                | set(self.expect.tools.unsuccessful)
                | set(self.expect.tools.count)
            )
            hidden = required - allowed
            if hidden:
                raise ValueError(
                    f"required tools are hidden by allowed_tools: {sorted(hidden)}"
                )
        return self


__all__ = [
    "VALID_GROUPS",
    "AnswerExpectation",
    "ApprovalPolicy",
    "ContextOverrides",
    "Expectation",
    "FileExpectation",
    "InitialFile",
    "InitialMessage",
    "InitialStep",
    "InitialTask",
    "Scenario",
    "StepExpectation",
    "TaskExpectation",
    "ToolExpectation",
]
