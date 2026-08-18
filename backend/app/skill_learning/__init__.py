"""Skill Learning V1：Completed Task Pattern Mining + Trace-backed
Procedure Distillation + Human-reviewed Skill Candidate。

领域边界：
- Task = 当前任务 / 最终任务事实的权威状态；
- Trace = Agent 实际执行过程的原始证据；
- SkillCandidate = 从多个历史 Task 提炼、尚未生效的候选过程知识；
- Skill = 经用户确认后正式生效的长期 procedural knowledge。

正式 Skill 只允许在 Candidate 被 accept 后由 Service 生成；本模块不修改
Task，也不在每个 Run 后调用模型（只有达到 batch_size 才触发 mining）。
"""

from .config import SkillLearningSettings
from .distiller import DistillationOutcome, ProcedureDistiller
from .evidence import TraceEvidenceBuilder
from .miner import PatternMiningOutcome, TaskPatternMiner
from .models import (
    PatternMiningResult,
    SkillCandidate,
    SkillCandidateAction,
    SkillCandidateStatus,
    TaskCard,
    TaskPatternCluster,
)
from .service import SkillLearningOutcome, SkillLearningService
from .store import MiningWatermark, SkillCandidateStore

__all__ = [
    "DistillationOutcome",
    "MiningWatermark",
    "PatternMiningOutcome",
    "PatternMiningResult",
    "ProcedureDistiller",
    "SkillCandidate",
    "SkillCandidateAction",
    "SkillCandidateStatus",
    "SkillCandidateStore",
    "SkillLearningOutcome",
    "SkillLearningService",
    "SkillLearningSettings",
    "TaskCard",
    "TaskPatternCluster",
    "TaskPatternMiner",
    "TraceEvidenceBuilder",
]
