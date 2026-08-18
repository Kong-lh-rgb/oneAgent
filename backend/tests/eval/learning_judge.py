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
    pattern_detected: bool | None = None
    action_correct: bool | None = None
    abstained: bool = False
    false_positive: bool = False
    duplicate_candidate: bool = False
    negative_scenario: bool = False
    positive_scenario: bool = False
    duplicate_scenario: bool = False
    counts_for_abstention: bool = False
    candidate_actions: tuple[str, ...] = ()
    candidate_names: tuple[str, ...] = ()
    pitfall_recall: float | None = None
    pitfall_found: tuple[str, ...] = ()
    # Deterministic Trace / Evidence / 阈值检查（learning-10+ 使用）。
    trace_steps_matched: bool = True
    evidence_missing: tuple[str, ...] = ()
    evidence_forbidden: tuple[str, ...] = ()
    cluster_threshold_passed: bool = True
    pitfall_threshold_passed: bool = True


def _pitfall_concept(keyword: str | tuple[str, ...]) -> tuple[str, ...]:
    """把期望 pitfall 关键词规范成 alias 组。

    单个字符串（旧格式）等价于 [该字符串]；list/tuple 视为同一 concept 的
    同义 alias 组。
    """
    if isinstance(keyword, str):
        return (keyword,)
    return tuple(keyword)


def judge_scenario(scenario, outcome) -> ScenarioVerdict:
    """对一次 Learning 运行给出 PASS / FAIL 与指标。

    指标口径（Eval 收口）：
    - Pattern Detection Recall：positive 场景（expected_pattern_task_aliases 非空）
      必须记 0 或 1，没发现 cluster 记 0，不能跳过；
    - Action Accuracy：只看 CREATE / UPDATE / NONE 是否正确（expected_action），
      不依赖 Skill 名字；
    - False Positive Rate：只除 negative 场景（no_candidates）；
    - Duplicate Rate：只除 duplicate 场景（expects_no_duplicate）。
    """

    expect = scenario.expect.learning
    env = outcome.environment
    candidates = list(outcome.candidates)
    mining = outcome.mining

    positive = bool(expect.expected_pattern_task_aliases)
    negative = expect.no_candidates
    duplicate_scenario = expect.expects_no_duplicate

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

    # 1) 无候选期望（negative 场景）
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
    #    Skill，并记录实际名字，名字差异不判 FAIL）。
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

    # 6) Pattern Detection（positive 场景必须记 0 或 1）
    pattern_detected: bool | None = None
    if positive:
        detected = any(
            set(cluster.task_ids) & expected_ids for cluster in clusters
        ) if expected_ids else bool(clusters)
        pattern_detected = bool(detected)
        if not detected:
            passed = False
            reasons.append(
                "pattern not detected (0 clusters or no overlap with expected tasks)"
            )

    # 7) Cluster Precision / Recall（按已产出 cluster 计算）
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

    # 8) Action Accuracy（只看 CREATE / UPDATE / NONE，不依赖名字）
    action_correct: bool | None = None
    if expect.expected_action is not None:
        if expect.expected_action == "none":
            action_correct = not candidates
        elif expect.expected_action == "create":
            action_correct = any(c.action.value == "create" for c in candidates)
        else:  # update
            action_correct = any(c.action.value == "update" for c in candidates)
        if not action_correct:
            passed = False
            reasons.append(
                f"action not {expect.expected_action}: "
                f"{[c.action.value for c in candidates]}"
            )

    # 9) Positive Abstention：positive 且期望 create/update 但无候选
    counts_for_abstention = bool(
        positive and expect.expected_action in ("create", "update")
    )
    abstained = bool(counts_for_abstention and not candidates)

    # 10) Duplicate（只对 duplicate 场景）：产出数量超过预置数量 = 重复创建
    if duplicate_scenario:
        seeded = len(scenario.initial_pending_candidates)
        if len(candidates) > seeded:
            duplicate = True
            passed = False
            reasons.append(
                f"duplicate candidate created (seeded={seeded}, "
                f"actual={len(candidates)})"
            )

    # 11) Pitfall Recall（支持中英文同义组：组内命中任一 alias 即算该 concept 命中）
    pitfall_recall: float | None = None
    pitfall_found: tuple[str, ...] = ()
    pitfall_concepts = [
        _pitfall_concept(keyword) for keyword in expect.expected_pitfall_keywords
    ]
    if pitfall_concepts and candidates:
        all_pitfalls = " ".join(
            " ".join(candidate.pitfalls) for candidate in candidates
        )
        all_pitfalls_lower = all_pitfalls.lower()
        hit_concepts: list[tuple[str, ...]] = []
        for concept in pitfall_concepts:
            if any(alias.lower() in all_pitfalls_lower for alias in concept):
                hit_concepts.append(concept)
        pitfall_found = tuple(
            concept[0] for concept in hit_concepts
        )
        pitfall_recall = len(hit_concepts) / len(pitfall_concepts)

    # 12) Deterministic Trace Steps（exact match，learning-10+）
    trace_steps_matched = True
    if expect.expected_trace_steps:
        for alias, expected_runs in expect.expected_trace_steps.items():
            actual_runs = outcome.trace_steps_by_alias.get(alias, {})
            for run_id, expected_steps in expected_runs.items():
                actual_steps = tuple(actual_runs.get(run_id, ()))
                if actual_steps != tuple(expected_steps):
                    trace_steps_matched = False
                    passed = False
                    reasons.append(
                        f"trace steps mismatch for {alias}/{run_id}: "
                        f"expected {list(expected_steps)}, got {list(actual_steps)}"
                    )

    # 13) Evidence 关键词（learning-10+）
    evidence_missing: list[str] = []
    evidence_forbidden: list[str] = []
    for alias, keywords in expect.evidence_contains.items():
        text = outcome.evidence_by_alias.get(alias, "")
        for keyword in keywords:
            if keyword.lower() not in text.lower():
                evidence_missing.append(f"{alias}:{keyword}")
    for alias, keywords in expect.evidence_not_contains.items():
        text = outcome.evidence_by_alias.get(alias, "")
        for keyword in keywords:
            if keyword.lower() in text.lower():
                evidence_forbidden.append(f"{alias}:{keyword}")
    if evidence_missing:
        passed = False
        reasons.append(f"evidence missing keywords: {sorted(evidence_missing)}")
    if evidence_forbidden:
        passed = False
        reasons.append(
            f"evidence contains forbidden keywords: {sorted(evidence_forbidden)}"
        )

    # 14) Cluster Precision / Recall 阈值（positive 场景，learning-10+）
    cluster_threshold_passed = True
    if positive and (
        expect.min_cluster_precision is not None
        or expect.min_cluster_recall is not None
    ):
        meets_threshold = any(
            cluster.precision is not None
            and cluster.recall is not None
            and (
                expect.min_cluster_precision is None
                or cluster.precision >= expect.min_cluster_precision
            )
            and (
                expect.min_cluster_recall is None
                or cluster.recall >= expect.min_cluster_recall
            )
            for cluster in cluster_evals
        )
        if not meets_threshold:
            cluster_threshold_passed = False
            passed = False
            reasons.append(
                "no cluster meets precision/recall threshold "
                f"(min_precision={expect.min_cluster_precision}, "
                f"min_recall={expect.min_cluster_recall})"
            )

    # 15) Pitfall Recall 阈值（learning-10+）
    pitfall_threshold_passed = True
    if (
        expect.min_pitfall_recall is not None
        and pitfall_recall is not None
        and pitfall_recall < expect.min_pitfall_recall
    ):
        pitfall_threshold_passed = False
        passed = False
        reasons.append(
            f"pitfall recall {pitfall_recall:.2f} below threshold "
            f"{expect.min_pitfall_recall}"
        )

    return ScenarioVerdict(
        scenario_id=scenario.id,
        passed=passed,
        reasons=reasons,
        clusters=cluster_evals,
        pattern_detected=pattern_detected,
        action_correct=action_correct,
        abstained=abstained,
        false_positive=false_positive,
        duplicate_candidate=duplicate,
        negative_scenario=negative,
        positive_scenario=positive,
        duplicate_scenario=duplicate_scenario,
        counts_for_abstention=counts_for_abstention,
        candidate_actions=tuple(c.action.value for c in candidates),
        candidate_names=tuple(c.proposed_name for c in candidates),
        pitfall_recall=pitfall_recall,
        pitfall_found=pitfall_found,
        trace_steps_matched=trace_steps_matched,
        evidence_missing=tuple(evidence_missing),
        evidence_forbidden=tuple(evidence_forbidden),
        cluster_threshold_passed=cluster_threshold_passed,
        pitfall_threshold_passed=pitfall_threshold_passed,
    )


@dataclass
class LiveSummary:
    """跨场景 / 跨 runs 的聚合指标。"""

    total_runs: int = 0
    passed_runs: int = 0
    precision_values: list[float] = field(default_factory=list)
    recall_values: list[float] = field(default_factory=list)
    # Pattern Detection Recall（场景级）：positive 场景 detected 数 / positive 场景数。
    pattern_detected_runs: int = 0
    pattern_positive_runs: int = 0
    # Action Accuracy：expected_action 定义的 CREATE / UPDATE / NONE。
    action_total: int = 0
    action_correct: int = 0
    # Positive Abstention：positive 期望 create/update 但无候选。
    abstained_runs: int = 0
    abstention_denominator: int = 0
    # False Positive：只除 negative 场景。
    false_positive_runs: int = 0
    negative_runs: int = 0
    # Duplicate：只除 duplicate 场景。
    duplicate_runs: int = 0
    duplicate_scenario_runs: int = 0
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
    def pattern_detection_recall(self) -> float | None:
        if not self.pattern_positive_runs:
            return None
        return self.pattern_detected_runs / self.pattern_positive_runs

    @property
    def action_accuracy(self) -> float | None:
        return self.action_correct / self.action_total if self.action_total else None

    @property
    def positive_abstention_rate(self) -> float | None:
        if not self.abstention_denominator:
            return None
        return self.abstained_runs / self.abstention_denominator

    @property
    def false_positive_rate(self) -> float | None:
        return (
            self.false_positive_runs / self.negative_runs
            if self.negative_runs
            else None
        )

    @property
    def duplicate_candidate_rate(self) -> float | None:
        return (
            self.duplicate_runs / self.duplicate_scenario_runs
            if self.duplicate_scenario_runs
            else None
        )

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
        """平均每个 eval batch（一次 run）的 tokens。"""
        return self.total_tokens / self.total_runs if self.total_runs else 0.0

    @property
    def avg_tokens_per_scanned_task(self) -> float | None:
        """平均每个被扫描 Task 的 tokens（仅统计触发了 mining 的 run）。"""
        return (
            self.total_tokens / self.total_scanned_tasks
            if self.total_scanned_tasks
            else None
        )


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
        if verdict.pattern_detected is not None:
            summary.pattern_positive_runs += 1
            if verdict.pattern_detected:
                summary.pattern_detected_runs += 1
        if verdict.action_correct is not None:
            summary.action_total += 1
            if verdict.action_correct:
                summary.action_correct += 1
        if verdict.abstained:
            summary.abstained_runs += 1
        if verdict.counts_for_abstention:
            summary.abstention_denominator += 1
        if verdict.negative_scenario:
            summary.negative_runs += 1
            if verdict.false_positive:
                summary.false_positive_runs += 1
        if verdict.duplicate_scenario:
            summary.duplicate_scenario_runs += 1
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
