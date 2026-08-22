"""Run Budget V1 的纯策略测试。"""

import pytest
from pydantic import ValidationError

from app.agent.budget import (
    RunBudget,
    RunBudgetConfig,
    RunBudgetStatus,
    chargeable_tokens,
)
from app.models.types import ModelUsage


def _config(**overrides: object) -> RunBudgetConfig:
    values = {
        "warning_tokens": 100,
        "finalization_tokens": 200,
        "hard_tokens": 300,
        "warning_model_calls": 4,
        "finalization_model_calls": 6,
        "hard_model_calls": 8,
        **overrides,
    }
    return RunBudgetConfig(_env_file=None, **values)


def test_chargeable_tokens_prefers_uncached_input() -> None:
    usage = ModelUsage(
        input_tokens=1_000,
        output_tokens=20,
        total_tokens=1_020,
        cached_input_tokens=900,
        uncached_input_tokens=100,
    )

    assert chargeable_tokens(usage) == 120


def test_chargeable_tokens_falls_back_to_all_input_when_cache_is_unknown() -> None:
    usage = ModelUsage(input_tokens=1_000, output_tokens=20, total_tokens=1_020)

    assert chargeable_tokens(usage) == 1_020


@pytest.mark.parametrize(
    ("usage", "status", "reason"),
    [
        (ModelUsage(input_tokens=10, model_calls=1), RunBudgetStatus.ACTIVE, None),
        (
            ModelUsage(input_tokens=100, model_calls=1),
            RunBudgetStatus.WARNING,
            "tokens",
        ),
        (
            ModelUsage(input_tokens=10, model_calls=6),
            RunBudgetStatus.FINALIZING,
            "model_calls",
        ),
        (
            ModelUsage(input_tokens=300, model_calls=1),
            RunBudgetStatus.EXCEEDED,
            "tokens",
        ),
    ],
)
def test_budget_stages(
    usage: ModelUsage,
    status: RunBudgetStatus,
    reason: str | None,
) -> None:
    decision = RunBudget(_config()).evaluate(usage)

    assert decision.status is status
    assert (decision.reason.value if decision.reason else None) == reason


def test_invalid_threshold_order_is_rejected() -> None:
    with pytest.raises(ValidationError, match="warning < finalization < hard"):
        _config(finalization_tokens=100)
