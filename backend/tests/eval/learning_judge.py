"""Skill Learning 真实模型结果的判定与指标计算。

只用于 Live Eval 汇报（不参与离线 pytest 的 run_checks）。
区分：
- 场景级 Verdict（PASS / FAIL + 原因）；
- 聚合指标（Cluster Precision/Recall、False Positive、Duplicate、Action Accuracy、
  Pitfall Recall）。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ClusterEval:
    """单个 cluster 的命中情况。"""

    pattern_name: str
    task_ids: tuple[str, ...]
    precision: float | None = None
    recall: float | None = None


@dataclass
class ScenarioVerdict:
    """一条场景某次运行的判定。"""

    scenario_id: str
    passed: bool
    reasons: list[str] = field(default_factory=list)
    clusters: list[ClusterEval] = field(default_factory=list)
    action_correct: bool | None = None
    false_positive: bool = False
    duplicate_candidate: bool = False
    candidate_actions: tuple[str, ...] = ()
    candidate_names: tuple[str, ...] = ()
    pitfall_recall: float | None = None
    pitfall_found: tuple[str, ...] = ()


def judge_scenario(scenario, outcome) -> ScenarioVerdict:
    """对一次 Learning 运行给出 PASS / FAIL 与指标。"""

    expect = scenario.expect.learning
    env = outcome.environment
    candidates = list(outcome.candidates)
    mining = outcome.mining

    actual_names = {candidate.proposed_name for candidate in candidates}
    expected_aliases = {
        alias
        for alias in expect.expected_pattern_task_aliases
        if alias in env.task_aliases
    }
    expected_ids = {env.task_aliases[alias] for alias in expected_aliases}
    clusters = list(mining.clusters) if mining is not None else []

    reasons: list[str] = []
    passed = True
    false_positive = False
    duplicate = False

    # 1) 无候选期望
    if expect.no_candidates:
        if candidates:
            passed = False
            false_positive = True
            reasons.append(f"expected no candidates, got {len(candidates)}")
        else:
            reasons.append("no candidates as expected")

    # 2) 候选数量
    if expect.candidate_count is not None and len(candidates) != expect.candidate_count:
        passed = False
        reasons.append(
            f"candidate_count={len(candidates)} expected {expect.candidate_count}"
        )

    # 3) 期望候选名（记录项）：真实模型常给出不同但语义合理的名字，
    #    名字差异不判 FAIL（动作与数量才是硬标准），但在报告里记录。
    if expect.expected_names:
        missing = set(expect.expected_names) - actual_names
        if missing:
            reasons.append(
                f"expected names {sorted(expect.expected_names)} not matched "
                f"(actual: {sorted(actual_names)})"
            )

    # 4) create / update 数量
    if expect.create_count is not None:
        actual = sum(1 for c in candidates if c.action.value == "create")
        if actual != expect.create_count:
            passed = False
            reasons.append(f"create_count={actual} expected {expect.create_count}")
    if expect.update_count is not None:
        actual = sum(1 for c in candidates if c.action.value == "update")
        if actual != expect.update_count:
            passed = False
            reasons.append(f"update_count={actual} expected {expect.update_count}")

    # 5) accept 后应 discover 的 Skill（Live 中候选名不可预知：只要求 accept 产生了
    #     Skill，并记录实际名字，名字差异不判 FAIL）。
    if expect.created_skill_names:
        if not outcome.created_skills:
            passed = False
            reasons.append("expected accepted skills but none were created")
        else:
            mismatch = set(expect.created_skill_names) - set(outcome.created_skills)
            if mismatch:
                reasons.append(
                    f"created skills {list(outcome.created_skills)} vs "
                    f"expected names {list(expect.created_skill_names)}"
                )

    # 6) Cluster Precision / Recall
    cluster_evals: list[ClusterEval] = []
    for cluster in clusters:
        ids = set(cluster.task_ids)
        if not ids or not expected_ids:
            precision: float | None = None
        else:
            precision = len(ids & expected_ids) / len(ids)
        recall = (
            len(ids & expected_ids) / len(expected_ids) if expected_ids else None
        )
        cluster_evals.append(
            ClusterEval(
                pattern_name=cluster.pattern_name,
                task_ids=cluster.task_ids,
                precision=precision,
                recall=recall,
            )
        )

    # 7) Action Accuracy（仅当期望名字且产出候选时）
    action_correct: bool | None = None
    if expect.expected_names:
        expected_action = "update" if expect.update_count else "create"
        matched = [
            c for c in candidates if c.proposed_name in set(expect.expected_names)
        ]
        if matched:
            action_correct = all(c.action.value == expected_action for c in matched)
            if not action_correct:
                passed = False
                reasons.append(
                    f"action not {expected_action}: "
                    f"{[c.action.value for c in matched]}"
                )

    # 8) Duplicate（同一期望名出现多次 = 重复创建）
    for expected_name in expect.expected_names:
        hits = [c for c in candidates if c.proposed_name == expected_name]
        if len(hits) > 1:
            duplicate = True
            if expect.candidate_count == 1:
                passed = False
                reasons.append(f"duplicate candidate created for {expected_name}")

    # 9) Pitfall Recall
    pitfall_recall: float | None = None
    pitfall_found: tuple[str, ...] = ()
    if expect.expected_pitfall_keywords and candidates:
        all_pitfalls = " ".join(
            " ".join(candidate.pitfalls) for candidate in candidates
        )
        found = [
            keyword
            for keyword in expect.expected_pitfall_keywords
            if keyword.lower() in all_pitfalls.lower()
        ]
        pitfall_found = tuple(found)
        pitfall_recall = len(found) / len(expect.expected_pitfall_keywords)

    return ScenarioVerdict(
        scenario_id=scenario.id,
        passed=passed,
        reasons=reasons,
        clusters=cluster_evals,
        action_correct=action_correct,
        false_positive=false_positive,
        duplicate_candidate=duplicate,
        candidate_actions=tuple(c.action.value for c in candidates),
        candidate_names=tuple(c.proposed_name for c in candidates),
        pitfall_recall=pitfall_recall,
        pitfall_found=pitfall_found,
    )


@dataclass
class LiveSummary:
    """跨场景 / 跨 runs 的聚合指标。"""

    total_runs: int = 0
    passed_runs: int = 0
    precision_values: list[float] = field(default_factory=list)
    recall_values: list[float] = field(default_factory=list)
    action_total: int = 0
    action_correct: int = 0
    false_positive_runs: int = 0
    duplicate_runs: int = 0
    pitfall_recalls: list[float] = field(default_factory=list)
    total_model_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    total_duration_ms: float = 0.0
    total_scanned_tasks: int = 0

    @property
    def pass_rate(self) -> float:
        return self.passed_runs / self.total_runs if self.total_runs else 0.0

    @property
    def avg_precision(self) -> float | None:
        if not self.precision_values:
            return None
        return sum(self.precision_values) / len(self.precision_values)

    @property
    def avg_recall(self) -> float | None:
        if not self.recall_values:
            return None
        return sum(self.recall_values) / len(self.recall_values)

    @property
    def action_accuracy(self) -> float | None:
        return self.action_correct / self.action_total if self.action_total else None

    @property
    def false_positive_rate(self) -> float:
        return self.false_positive_runs / self.total_runs if self.total_runs else 0.0

    @property
    def duplicate_candidate_rate(self) -> float:
        return self.duplicate_runs / self.total_runs if self.total_runs else 0.0

    @property
    def avg_pitfall_recall(self) -> float | None:
        if not self.pitfall_recalls:
            return None
        return sum(self.pitfall_recalls) / len(self.pitfall_recalls)

    @property
    def avg_duration_ms(self) -> float:
        return self.total_duration_ms / self.total_runs if self.total_runs else 0.0

    @property
    def avg_tokens_per_batch(self) -> float:
        return self.total_tokens / self.total_runs if self.total_runs else 0.0


def aggregate(verdicts: list[ScenarioVerdict], outcomes: list) -> LiveSummary:
    """聚合多次运行的指标。"""

    summary = LiveSummary()
    for verdict, outcome in zip(verdicts, outcomes):
        summary.total_runs += 1
        if verdict.passed:
            summary.passed_runs += 1
        summary.precision_values.extend(
            cluster.precision
            for cluster in verdict.clusters
            if cluster.precision is not None
        )
        summary.recall_values.extend(
            cluster.recall
            for cluster in verdict.clusters
            if cluster.recall is not None
        )
        if verdict.action_correct is not None:
            summary.action_total += 1
            if verdict.action_correct:
                summary.action_correct += 1
        if verdict.false_positive:
            summary.false_positive_runs += 1
        if verdict.duplicate_candidate:
            summary.duplicate_runs += 1
        if verdict.pitfall_recall is not None:
            summary.pitfall_recalls.append(verdict.pitfall_recall)
        if outcome is not None:
            mining = getattr(outcome, "mining", None)
            summary.total_scanned_tasks += (
                getattr(mining, "scanned_task_count", 0) or 0
            )
            summary.total_model_calls += (
                getattr(mining, "pattern_mining_calls", 0) or 0
            ) + (getattr(mining, "distillation_calls", 0) or 0)
            summary.total_input_tokens += getattr(mining, "input_tokens", 0) or 0
            summary.total_output_tokens += getattr(mining, "output_tokens", 0) or 0
            summary.total_tokens += getattr(mining, "total_tokens", 0) or 0
            summary.total_duration_ms += (
                getattr(mining, "total_duration_ms", 0.0) or 0.0
            )
    return summary


__all__ = [
    "ClusterEval",
    "LiveSummary",
    "ScenarioVerdict",
    "aggregate",
    "judge_scenario",
]
