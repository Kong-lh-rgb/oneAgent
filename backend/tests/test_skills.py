"""Skill 系统的离线测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.skills import SkillStore, parse_skill_markdown, register_skill_tools
from app.tools.registry import ToolRegistry


def _skill_text(
    name: str,
    description: str,
    content: str,
) -> str:
    return (
        f"---\nname: {name}\ndescription: {description}\n---\n\n{content}"
    )


def _write_skill(root: Path, name: str, content: str) -> Path:
    path = root / f"{name}.md"
    path.write_text(content, encoding="utf-8")
    return path


def test_parse_skill_markdown_reads_front_matter_and_body() -> None:
    skill = parse_skill_markdown(
        _skill_text(
            "debug_python",
            "排查 Python 报错的标准流程。",
            "# Debug Python\n\n1. 复现\n2. 查看 traceback",
        )
    )

    assert skill.name == "debug_python"
    assert skill.description == "排查 Python 报错的标准流程。"
    assert "1. 复现" in skill.content


def test_parse_skill_markdown_rejects_missing_front_matter() -> None:
    with pytest.raises(ValueError, match="front matter"):
        parse_skill_markdown("# 没有 front matter 的文档")


def test_parse_skill_markdown_rejects_invalid_name() -> None:
    with pytest.raises(ValueError):
        parse_skill_markdown(
            _skill_text("Debug Python", "desc", "内容")
        )


@pytest.mark.asyncio
async def test_store_lists_skills_sorted_by_name(tmp_path: Path) -> None:
    store = SkillStore(tmp_path)
    await store.initialize()
    _write_skill(
        tmp_path,
        "deploy",
        _skill_text("deploy", "部署流程", "1. 构建\n2. 发布"),
    )
    _write_skill(
        tmp_path,
        "debug",
        _skill_text("debug", "调试流程", "1. 复现"),
    )

    skills = await store.list()

    assert [skill.name for skill in skills] == ["debug", "deploy"]
    assert skills[0].description == "调试流程"


@pytest.mark.asyncio
async def test_store_load_returns_none_when_missing(tmp_path: Path) -> None:
    store = SkillStore(tmp_path)
    await store.initialize()

    assert await store.load("not_exist") is None


@pytest.mark.asyncio
async def test_store_rejects_file_name_mismatch(tmp_path: Path) -> None:
    store = SkillStore(tmp_path)
    await store.initialize()
    # 文件名与 front matter name 不一致 → 跳过并视为不存在。
    _write_skill(
        tmp_path,
        "wrong",
        _skill_text("right_name", "desc", "内容"),
    )

    assert await store.load("wrong") is None
    assert await store.list() == ()


@pytest.mark.asyncio
async def test_skill_tools_list_and_read(tmp_path: Path) -> None:
    store = SkillStore(tmp_path)
    await store.initialize()
    _write_skill(
        tmp_path,
        "debug_python",
        _skill_text("debug_python", "排查 Python 报错", "1. 复现\n2. 看日志"),
    )
    registry = ToolRegistry()
    register_skill_tools(registry, store)

    listed = await registry.get("skill_list").execute({})
    assert listed["skills"] == [
        {
            "name": "debug_python",
            "description": "排查 Python 报错",
        }
    ]

    read = await registry.get("skill_read").execute(
        {"name": "debug_python"}
    )
    assert read["found"] is True
    assert "2. 看日志" in read["content"]

    missing = await registry.get("skill_read").execute(
        {"name": "no_such_skill"}
    )
    assert missing["found"] is False


@pytest.mark.asyncio
async def test_skill_read_validates_required_name(tmp_path: Path) -> None:
    store = SkillStore(tmp_path)
    await store.initialize()
    registry = ToolRegistry()
    register_skill_tools(registry, store)

    with pytest.raises(ValueError, match="'name'"):
        await registry.get("skill_read").execute({})
