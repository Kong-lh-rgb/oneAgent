"""场景 YAML 加载与校验。"""

from __future__ import annotations

from pathlib import Path

import yaml

from .scenario import Scenario

_SCENARIOS_DIR = Path(__file__).resolve().parent / "scenarios"


def load_scenario(path: str | Path) -> Scenario:
    """从单个 YAML 文件加载并校验场景。"""

    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"场景必须是映射（dict）：{path}")
    return Scenario.model_validate(payload)


def load_scenarios(
    scenarios_dir: str | Path = _SCENARIOS_DIR,
) -> tuple[Scenario, ...]:
    """递归加载目录下全部 .yaml/.yml 场景（支持按分组的子目录）。"""

    directory = Path(scenarios_dir)
    paths = sorted(
        path
        for path in directory.rglob("*.y*ml")
        if path.suffix.lower() in (".yaml", ".yml")
    )
    scenarios = tuple(load_scenario(path) for path in paths)
    ids = [scenario.id for scenario in scenarios]
    duplicates = sorted(
        {scenario_id for scenario_id in ids if ids.count(scenario_id) > 1}
    )
    if duplicates:
        raise ValueError(f"场景 ID 不得重复：{duplicates}")
    return scenarios


def select_scenarios(
    scenarios: tuple[Scenario, ...],
    *,
    scenario_ids: tuple[str, ...] = (),
    groups: tuple[str, ...] = (),
) -> tuple[Scenario, ...]:
    """按 ID 与分组过滤场景；两者皆空时返回全部。"""

    selected = scenarios
    if scenario_ids:
        wanted = set(scenario_ids)
        selected = tuple(scenario for scenario in selected if scenario.id in wanted)
    if groups:
        wanted = set(groups)
        selected = tuple(scenario for scenario in selected if scenario.group in wanted)
    return selected


__all__ = [
    "load_scenario",
    "load_scenarios",
    "select_scenarios",
]
