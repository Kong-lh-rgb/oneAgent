"""长期记忆多阶段场景加载器。"""

from __future__ import annotations

from pathlib import Path

import yaml

from .scenario import MemoryEvalScenario

_SCENARIOS_DIR = Path(__file__).resolve().parent / "scenarios"


def load_scenario(path: str | Path) -> MemoryEvalScenario:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Memory 场景必须是映射：{path}")
    return MemoryEvalScenario.model_validate(payload)


def load_scenarios(
    scenarios_dir: str | Path = _SCENARIOS_DIR,
) -> tuple[MemoryEvalScenario, ...]:
    directory = Path(scenarios_dir)
    scenarios = tuple(
        load_scenario(path)
        for path in sorted(directory.rglob("*.y*ml"))
        if path.suffix.lower() in {".yaml", ".yml"}
    )
    ids = [scenario.id for scenario in scenarios]
    duplicates = sorted({value for value in ids if ids.count(value) > 1})
    if duplicates:
        raise ValueError(f"Memory 场景 ID 不得重复：{duplicates}")
    return scenarios


def select_scenarios(
    scenarios: tuple[MemoryEvalScenario, ...],
    *,
    scenario_ids: tuple[str, ...] = (),
    tags: tuple[str, ...] = (),
) -> tuple[MemoryEvalScenario, ...]:
    selected = scenarios
    if scenario_ids:
        wanted = set(scenario_ids)
        selected = tuple(item for item in selected if item.id in wanted)
    if tags:
        wanted_tags = set(tags)
        selected = tuple(item for item in selected if wanted_tags & set(item.tags))
    return selected


__all__ = ["load_scenario", "load_scenarios", "select_scenarios"]
