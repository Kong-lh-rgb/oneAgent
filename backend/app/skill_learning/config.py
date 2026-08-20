"""Skill Learning 运行配置。"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"

_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / ".vesta" / "skill-learning"


class SkillLearningSettings(BaseSettings):
    """Skill Learning V1 的触发、模型与落盘配置。"""

    model_config = SettingsConfigDict(
        env_file=_BACKEND_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 总开关；关闭时 maybe_run_mining 直接跳过。
    skill_learning_enabled: bool = True
    # 每累计多少个新的 Completed Task 才触发一次 Pattern Mining。
    # 这是"扫描周期"，不是"必须生成 Skill"的条件。
    skill_learning_batch_size: int = Field(default=20, ge=1)
    # Cluster 至少包含的任务数；频率不是唯一依据，但这是下限。
    skill_learning_min_cluster_size: int = Field(default=3, ge=2)
    # 一次扫描最多处理的 Task 数（保护输入规模）。
    skill_learning_max_tasks_per_scan: int = Field(default=20, ge=1)
    # Pattern Mining 失败的最大重试次数；达到上限后放弃该 batch（避免无限重试）。
    skill_learning_max_attempts: int = Field(default=3, ge=1)
    # 供 Pattern Mining / Distillation 使用的模型（缺省走默认 provider/model）。
    skill_learning_provider: str | None = None
    skill_learning_model: str | None = None
    skill_learning_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    skill_learning_max_output_tokens: int = Field(default=2_000, ge=1)
    skill_learning_timeout_seconds: float = Field(default=60.0, gt=0.0)
    # 关闭 reasoning（thinking）的 extra_body；None=自动（仅对实测支持的
    # Provider 生效）。
    skill_learning_disable_thinking: bool | None = None
    # Candidate Accept 时默认落到的 Skill scope（project / user）。
    skill_learning_default_scope: str = "project"
    # 候选与 watermark 数据目录。
    skill_learning_data_dir: Path = _DEFAULT_DATA_DIR
    # 模型调用最多只消费多少条 Trace 事件（Evidence Builder 上限）。
    skill_learning_max_events_per_task: int = Field(default=200, ge=1)
    # Evidence 文本单 Task 长度上限（字符）。
    skill_learning_max_evidence_chars: int = Field(default=3_000, ge=200)


__all__ = ["SkillLearningSettings"]
