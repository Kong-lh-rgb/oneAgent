"""Skill Runtime V2 的离线测试。

覆盖：名称校验、SKILL.md 解析、路径安全、双层发现、坏 Skill 降级、
激活加载、Context Provider（Catalog / Active / Budget）、工具行为、
以及 Runtime 集成（Run-scoped 激活 + 事件）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from app.agent.events import (
    AgentEventType,
    InMemoryEventHandler,
)
from app.agent.runtime import AgentRuntime
from app.models.adapter import ModelAdapter
from app.models.config import ModelSettings, ProviderConfig
from app.models.registry import ModelAdapterRegistry
from app.models.types import (
    ApiStyle,
    Message,
    MessageRole,
    ModelResponse,
    ModelUsage,
    ToolCall,
)
from app.skills import (
    ACTIVE_SKILL_MESSAGE_NAME,
    SKILL_CATALOG_MESSAGE_NAME,
    SKILL_READ_TOOL_NAME,
    Skill,
    SkillContextProvider,
    SkillMetadata,
    SkillResources,
    SkillScope,
    SkillStore,
    parse_skill_document,
    register_skill_tools,
    safe_skill_dir,
    safe_skill_file,
    safe_skill_resource,
    valid_skill_name,
    validate_skill_name,
)
from app.skills.parser import SkillParseError
from app.tools.registry import ToolRegistry


class _FakeAdapter(ModelAdapter):
    """离线假模型：按顺序弹出一条响应。"""

    def __init__(
        self,
        config: ProviderConfig,
        responses: list[ModelResponse | Exception],
    ) -> None:
        super().__init__(config)
        self.responses = list(responses)
        self.requests: list[Message] = []

    async def complete(self, request) -> ModelResponse:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def close(self) -> None:
        pass


def _model_response(
    *,
    content: str | None = None,
    tool_calls: tuple[ToolCall, ...] = (),
) -> ModelResponse:
    return ModelResponse(
        id="fake-response",
        provider="fake",
        model="fake-model",
        message=Message(
            role=MessageRole.ASSISTANT,
            content=content,
            tool_calls=tool_calls,
        ),
        usage=ModelUsage(),
    )


def _fake_registry(
    responses: list[ModelResponse | Exception],
) -> tuple[ModelAdapterRegistry, _FakeAdapter]:
    config = ProviderConfig(
        provider="fake",
        model="fake-model",
        api_key=SecretStr("offline-test-key"),
        api_style=ApiStyle.CHAT_COMPLETIONS,
    )
    adapter = _FakeAdapter(config, responses)
    registry = ModelAdapterRegistry(ModelSettings(_env_file=None))
    registry.register("fake", lambda _: adapter, config=config)
    return registry, adapter

# ---------------------------------------------------------------------------
# 名称校验
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "debug-python",
        "code-review",
        "skill1",
        "a",
        "a-b-c",
    ],
)
def test_validate_skill_name_accepts_valid_names(name: str) -> None:
    assert validate_skill_name(name) == name
    assert valid_skill_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "",
        "Debug-Python",  # 大写
        "debug_python",  # 下划线
        "-debug",  # 首连字符
        "debug-",  # 尾连字符
        "debug--python",  # 连续连字符
        "debug python",  # 空格
        "x" * 65,  # 超长
    ],
)
def test_validate_skill_name_rejects_invalid_names(name: str) -> None:
    assert not valid_skill_name(name)
    with pytest.raises(ValueError):
        validate_skill_name(name)


# ---------------------------------------------------------------------------
# SKILL.md 解析
# ---------------------------------------------------------------------------


def _skill_text(
    name: str,
    description: str,
    body: str = "内容",
    **extra: object,
) -> str:
    lines = ["---", f"name: {name}", f"description: {description}"]
    for key, value in extra.items():
        if isinstance(value, str):
            lines.append(f"{key}: {value}")
        elif isinstance(value, list):
            items = "\n".join(f"  - {item}" for item in value)
            lines.append(f"{key}:\n{items}")
        elif isinstance(value, dict):
            items = "\n".join(f"  {k}: {v}" for k, v in value.items())
            lines.append(f"{key}:\n{items}")
    lines.append("---")
    lines.append("")
    lines.append(body)
    return "\n".join(lines)


def test_parse_skill_document_reads_full_metadata() -> None:
    parsed = parse_skill_document(
        _skill_text(
            "code-review",
            "评审代码",
            body="# 评审\n\n1. 正确性",
            license="MIT",
            compatibility='">=3.10"',
            metadata={"audience": "developer"},
            **{"allowed-tools": ["read_file", "write_file"]},
        ),
        expected_name="code-review",
    )

    assert parsed.name == "code-review"
    assert parsed.description == "评审代码"
    assert parsed.license == "MIT"
    assert parsed.compatibility == ">=3.10"
    assert parsed.metadata == {"audience": "developer"}
    assert parsed.allowed_tools == ("read_file", "write_file")
    assert parsed.body.startswith("# 评审")


def test_parse_skill_document_trims_description_and_body() -> None:
    parsed = parse_skill_document(
        "---\nname: demo\ndescription:   描述  \n---\n\n  正文  \n",
        expected_name="demo",
    )
    assert parsed.description == "描述"
    assert parsed.body == "正文"


@pytest.mark.parametrize(
    "text, reason",
    [
        ("# 没有 front matter", "missing YAML front matter"),
        ("---\nname: demo\ndescription: 描述\n---", "empty skill body"),
    ],
)
def test_parse_skill_document_rejects_bad_structure(
    text: str,
    reason: str,
) -> None:
    with pytest.raises(SkillParseError, match=reason):
        parse_skill_document(text, expected_name="demo")


def test_parse_skill_document_rejects_invalid_yaml() -> None:
    with pytest.raises(SkillParseError, match="invalid YAML"):
        parse_skill_document(
            "---\nname: [unclosed\ndescription: 描述\n---\n\n正文",
            expected_name="demo",
        )


def test_parse_skill_document_rejects_name_mismatch() -> None:
    with pytest.raises(SkillParseError, match="does not match"):
        parse_skill_document(
            _skill_text("other-name", "描述"),
            expected_name="demo",
        )


def test_parse_skill_document_rejects_invalid_name() -> None:
    with pytest.raises(SkillParseError, match="hyphens"):
        parse_skill_document(
            _skill_text("debug_python", "描述"),
            expected_name="debug_python",
        )


def test_parse_skill_document_rejects_missing_or_empty_description() -> None:
    with pytest.raises(SkillParseError, match="description"):
        parse_skill_document(
            "---\nname: demo\n---\n\n正文",
            expected_name="demo",
        )
    # YAML 中空值会被解析为 None → 报 missing/non-string。
    with pytest.raises(SkillParseError, match="non-string"):
        parse_skill_document(
            "---\nname: demo\ndescription:   \n---\n\n正文",
            expected_name="demo",
        )


def test_parse_skill_document_rejects_overlong_description() -> None:
    with pytest.raises(SkillParseError, match="1024"):
        parse_skill_document(
            _skill_text("demo", "x" * 1025),
            expected_name="demo",
        )


def test_parse_skill_document_rejects_non_mapping_metadata() -> None:
    with pytest.raises(SkillParseError, match="metadata"):
        parse_skill_document(
            "---\nname: demo\ndescription: 描述\nmetadata: [1, 2]\n---\n\n正文",
            expected_name="demo",
        )


@pytest.mark.parametrize(
    "allowed",
    [
        "read_file",  # 非列表
        ["read_file", ""],  # 空字符串项
        [1, 2],  # 非字符串项
    ],
)
def test_parse_skill_document_rejects_bad_allowed_tools(allowed: object) -> None:
    with pytest.raises(SkillParseError, match="allowed-tools"):
        parse_skill_document(
            "---\nname: demo\ndescription: 描述\n"
            f"allowed-tools: {allowed}\n---\n\n正文",
            expected_name="demo",
        )


# ---------------------------------------------------------------------------
# 路径安全
# ---------------------------------------------------------------------------


def test_safe_skill_dir_accepts_valid_dir(tmp_path: Path) -> None:
    (tmp_path / "demo").mkdir()
    result = safe_skill_dir(tmp_path, "demo")
    assert result is not None
    assert result == (tmp_path / "demo").resolve()


def test_safe_skill_dir_rejects_escape_and_symlink(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)

    assert safe_skill_dir(tmp_path, "..") is None
    assert safe_skill_dir(tmp_path, "link") is None
    assert safe_skill_dir(tmp_path, "missing") is None


def test_safe_skill_file_rejects_symlink(tmp_path: Path) -> None:
    skill_dir = tmp_path / "demo"
    skill_dir.mkdir()
    target = tmp_path / "elsewhere.txt"
    target.write_text("x", encoding="utf-8")
    (skill_dir / "SKILL.md").symlink_to(target)

    assert safe_skill_file(skill_dir) is None


def test_safe_skill_resource_accepts_internal_file(tmp_path: Path) -> None:
    skill_dir = tmp_path / "demo"
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "references" / "a.md").write_text("x", encoding="utf-8")

    result = safe_skill_resource(skill_dir, "references/a.md")
    assert result == (skill_dir / "references" / "a.md").resolve()


@pytest.mark.parametrize(
    "relative",
    ["../secret.txt", "/etc/passwd", "a/../../secret.txt", "a/../b"],
)
def test_safe_skill_resource_rejects_escape(
    tmp_path: Path,
    relative: str,
) -> None:
    skill_dir = tmp_path / "demo"
    skill_dir.mkdir()
    assert safe_skill_resource(skill_dir, relative) is None


def test_safe_skill_resource_rejects_symlink_and_directory(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "demo"
    (skill_dir / "refs").mkdir(parents=True)
    target = tmp_path / "outside.txt"
    target.write_text("x", encoding="utf-8")
    (skill_dir / "refs" / "link.txt").symlink_to(target)
    (skill_dir / "refs" / "subdir").mkdir()

    assert safe_skill_resource(skill_dir, "refs/link.txt") is None
    assert safe_skill_resource(skill_dir, "refs/subdir") is None
    assert safe_skill_resource(skill_dir, "refs/missing.txt") is None


# ---------------------------------------------------------------------------
# Store：双层发现 + 激活加载 + 坏 Skill 降级
# ---------------------------------------------------------------------------


def _write_skill_dir(
    root: Path,
    name: str,
    *,
    description: str = "描述",
    body: str = "# 正文\n\n步骤 1",
) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    content = (
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}"
    )
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    return skill_dir


async def _make_store(tmp_path: Path) -> SkillStore:
    store = SkillStore(tmp_path / "user", tmp_path / "project")
    await store.initialize()
    return store


@pytest.mark.asyncio
async def test_store_catalog_sorts_and_merges_project_over_user(
    tmp_path: Path,
) -> None:
    store = await _make_store(tmp_path)
    _write_skill_dir(store.user_dir, "alpha", description="用户版 alpha")
    _write_skill_dir(store.user_dir, "beta", description="用户版 beta")
    _write_skill_dir(store.project_dir, "alpha", description="项目版 alpha")

    catalog = await store.catalog()

    assert [m.name for m in catalog] == ["alpha", "beta"]
    alpha = next(m for m in catalog if m.name == "alpha")
    assert alpha.scope is SkillScope.PROJECT
    assert alpha.description == "项目版 alpha"
    beta = next(m for m in catalog if m.name == "beta")
    assert beta.scope is SkillScope.USER


@pytest.mark.asyncio
async def test_store_catalog_skips_bad_skills_with_diagnostics(
    tmp_path: Path,
) -> None:
    store = await _make_store(tmp_path)
    bad_dir = store.project_dir / "bad_name"
    bad_dir.mkdir(parents=True)
    (bad_dir / "SKILL.md").write_text(
        "---\nname: bad_name\ndescription: 坏\n---\n\n正文",
        encoding="utf-8",
    )
    _write_skill_dir(store.project_dir, "good", description="好")
    broken_dir = store.project_dir / "broken"
    broken_dir.mkdir()
    (broken_dir / "SKILL.md").write_text(
        "---\nname: broken\n---\n\n正文",
        encoding="utf-8",
    )

    catalog = await store.catalog()
    diagnostics = store.diagnostics()

    assert [m.name for m in catalog] == ["good"]
    assert any("bad_name" in d.render() for d in diagnostics)
    assert any("broken" in d.render() for d in diagnostics)


@pytest.mark.asyncio
async def test_store_load_returns_none_when_missing_or_bad(
    tmp_path: Path,
) -> None:
    store = await _make_store(tmp_path)
    assert await store.load("missing") is None

    _write_skill_dir(store.project_dir, "bad", description="")
    assert await store.load("bad") is None


@pytest.mark.asyncio
async def test_store_load_returns_full_skill_with_resources(
    tmp_path: Path,
) -> None:
    store = await _make_store(tmp_path)
    skill_dir = _write_skill_dir(
        store.project_dir,
        "research",
        description="研究",
        body="# 研究\n\n步骤",
    )
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / "tpl.md").write_text("模板", encoding="utf-8")
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "run.sh").write_text("#!/bin/sh", encoding="utf-8")

    skill = await store.load("research")

    assert skill is not None
    assert isinstance(skill, Skill)
    assert skill.metadata.name == "research"
    assert skill.root == skill_dir.resolve()
    assert skill.resources.references == ("references/tpl.md",)
    assert skill.resources.scripts == ("scripts/run.sh",)
    assert skill.resources.assets == ()
    assert "步骤" in skill.content


def test_skill_render_instructions_lists_resources() -> None:
    metadata = SkillMetadata(
        name="demo",
        description="描述",
        scope=SkillScope.PROJECT,
        location=Path("/tmp/demo/SKILL.md"),
    )
    skill = Skill(
        metadata=metadata,
        content="# 正文",
        root=Path("/tmp/demo"),
        resources=SkillResources(references=("references/a.md",)),
    )
    rendered = skill.render_instructions()
    assert rendered.startswith("# Skill: demo")
    assert "references/a.md" in rendered
    assert "skill_resource_read" in rendered


# ---------------------------------------------------------------------------
# Context Provider：Catalog / Active / Budget
# ---------------------------------------------------------------------------


def _metadata(name: str, description: str = "描述") -> SkillMetadata:
    return SkillMetadata(
        name=name,
        description=description,
        scope=SkillScope.PROJECT,
        location=Path(f"/tmp/{name}/SKILL.md"),
    )


def _skill(name: str, content: str = "# 正文\n\n步骤") -> Skill:
    return Skill(
        metadata=_metadata(name),
        content=content,
        root=Path(f"/tmp/{name}"),
    )


def test_catalog_message_injects_system_message_with_entries() -> None:
    provider = SkillContextProvider(max_tokens=4096, max_active=4)
    message = provider.catalog_message((_metadata("a"), _metadata("b")))

    assert message is not None
    assert message.role is MessageRole.SYSTEM
    assert message.name == SKILL_CATALOG_MESSAGE_NAME
    assert "[a] 描述" in (message.content or "")
    assert "[b] 描述" in (message.content or "")


def test_catalog_message_handles_empty_catalog() -> None:
    provider = SkillContextProvider(max_tokens=4096, max_active=4)
    message = provider.catalog_message(())
    assert message is not None
    assert "No skills available" in (message.content or "")


def test_active_messages_dedupes_and_preserves_order() -> None:
    provider = SkillContextProvider(max_tokens=4096, max_active=4)
    messages = provider.active_messages((_skill("a"), _skill("b"), _skill("a")))

    assert [m.name for m in messages] == [ACTIVE_SKILL_MESSAGE_NAME] * 2
    assert "Skill: a" in (messages[0].content or "")
    assert "Skill: b" in (messages[1].content or "")


def test_active_tokens_counts_instructions() -> None:
    provider = SkillContextProvider(max_tokens=4096, max_active=4)
    single = provider.active_tokens((_skill("a"),))
    both = provider.active_tokens((_skill("a"), _skill("b")))
    assert single > 0
    assert both > single


def test_would_exceed_budget_respects_max_active() -> None:
    provider = SkillContextProvider(max_tokens=1_000_000, max_active=2)
    assert not provider.would_exceed_budget((_skill("a"),), _skill("b"))
    assert provider.would_exceed_budget((_skill("a"), _skill("b")), _skill("c"))


def test_would_exceed_budget_respects_token_limit() -> None:
    provider = SkillContextProvider(max_tokens=10, max_active=4)
    assert provider.would_exceed_budget((), _skill("a"))


def test_would_exceed_budget_dedupes_existing_skill() -> None:
    provider = SkillContextProvider(max_tokens=100, max_active=4)
    existing = _skill("a")
    assert not provider.would_exceed_budget((existing,), existing)


# ---------------------------------------------------------------------------
# 工具：skill_read / skill_resource_read
# ---------------------------------------------------------------------------


async def _tool_registry_with_store(tmp_path: Path) -> tuple[ToolRegistry, SkillStore]:
    store = await _make_store(tmp_path)
    registry = ToolRegistry()
    register_skill_tools(registry, store)
    return registry, store


@pytest.mark.asyncio
async def test_skill_read_returns_skill_and_missing(tmp_path: Path) -> None:
    registry, _ = await _tool_registry_with_store(tmp_path)
    _write_skill_dir(tmp_path / "project", "demo", description="演示")

    found = await registry.get(SKILL_READ_TOOL_NAME).execute({"name": "demo"})
    assert found["found"] is True
    assert found["name"] == "demo"
    assert found["scope"] == "project"
    assert "步骤" in found["content"]

    missing = await registry.get(SKILL_READ_TOOL_NAME).execute(
        {"name": "nope"}
    )
    assert missing["found"] is False


@pytest.mark.asyncio
async def test_skill_read_validates_name_argument(tmp_path: Path) -> None:
    registry, _ = await _tool_registry_with_store(tmp_path)
    with pytest.raises(ValueError, match="'name'"):
        await registry.get(SKILL_READ_TOOL_NAME).execute({})


@pytest.mark.asyncio
async def test_skill_resource_read_reads_managed_resource(
    tmp_path: Path,
) -> None:
    registry, _ = await _tool_registry_with_store(tmp_path)
    skill_dir = _write_skill_dir(
        tmp_path / "project",
        "research",
        description="研究",
    )
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / "tpl.md").write_text("模板内容", encoding="utf-8")

    result = await registry.get("skill_resource_read").execute(
        {"name": "research", "path": "references/tpl.md"}
    )
    assert result["found"] is True
    assert result["content"] == "模板内容"


@pytest.mark.asyncio
async def test_skill_resource_read_rejects_escape_path(
    tmp_path: Path,
) -> None:
    registry, _ = await _tool_registry_with_store(tmp_path)
    _write_skill_dir(tmp_path / "project", "research", description="研究")

    result = await registry.get("skill_resource_read").execute(
        {"name": "research", "path": "../SKILL.md"}
    )
    assert result["found"] is False
    assert "escape" in result["error"]


# ---------------------------------------------------------------------------
# Runtime 集成：Run-scoped 激活 + 事件 + 指令注入
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runtime_activates_skill_and_injects_instructions(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "project" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: 演示\n---\n\n# Demo\n\n按演示流程操作",
        encoding="utf-8",
    )
    store = SkillStore(tmp_path / "user", tmp_path / "project")
    await store.initialize()
    registry = ToolRegistry()
    register_skill_tools(registry, store)
    provider = SkillContextProvider(max_tokens=4096, max_active=4)

    # 第一轮：模型调用 skill_read；第二轮：输出最终文本。
    responses = [
        _model_response(
            tool_calls=(
                ToolCall(
                    id="skill-1",
                    name=SKILL_READ_TOOL_NAME,
                    arguments={"name": "demo"},
                ),
            )
        ),
        _model_response(content="已完成"),
    ]
    model_registry, adapter = _fake_registry(responses)
    events = InMemoryEventHandler()
    runtime = AgentRuntime(
        model_registry,
        registry,
        provider="fake",
        skill_store=store,
        skill_context_provider=provider,
    )

    await runtime.run(
        "请处理演示任务",
        event_handler=events,
    )

    # 事件：激活成功 + MODEL_STARTED 携带 skill 观测字段。
    activated = [
        e for e in events.events if e.type is AgentEventType.SKILL_ACTIVATED
    ]
    assert len(activated) == 1
    assert activated[0].skill_name == "demo"
    assert activated[0].skill_scope == "project"
    assert activated[0].active_skill_names == ("demo",)
    assert activated[0].active_skill_tokens and activated[0].active_skill_tokens > 0

    started = [
        e for e in events.events if e.type is AgentEventType.MODEL_STARTED
    ]
    # 最后一个 MODEL_STARTED 对应激活后的 step，应携带 skill 观测字段。
    assert started[-1].active_skill_names == ("demo",)
    assert started[-1].available_skill_count == 1
    assert started[-1].skill_catalog_tokens and started[-1].skill_catalog_tokens > 0
    assert started[-1].active_skill_tokens and started[-1].active_skill_tokens > 0

    # 第二轮请求应包含 Active Skill 指令与 Catalog 消息。
    second_request = adapter.requests[1]
    injected = [
        m
        for m in second_request.messages
        if m.name in (ACTIVE_SKILL_MESSAGE_NAME, SKILL_CATALOG_MESSAGE_NAME)
    ]
    assert any(m.name == ACTIVE_SKILL_MESSAGE_NAME for m in injected)
    assert any("Skill: demo" in (m.content or "") for m in injected)
    assert any(m.name == SKILL_CATALOG_MESSAGE_NAME for m in injected)


@pytest.mark.asyncio
async def test_runtime_emits_activation_failed_when_budget_exceeded(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "project" / "big"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: big\ndescription: 大\n---\n\n# Big\n\n" + "x" * 200,
        encoding="utf-8",
    )
    store = SkillStore(tmp_path / "user", tmp_path / "project")
    await store.initialize()
    registry = ToolRegistry()
    register_skill_tools(registry, store)
    provider = SkillContextProvider(max_tokens=5, max_active=4)

    tc = ToolCall(
        id="skill-1",
        name=SKILL_READ_TOOL_NAME,
        arguments={"name": "big"},
    )
    model_registry, _ = _fake_registry(
        [
            _model_response(tool_calls=(tc,)),
            _model_response(content="done"),
        ]
    )
    events = InMemoryEventHandler()
    runtime = AgentRuntime(
        model_registry,
        registry,
        provider="fake",
        skill_store=store,
        skill_context_provider=provider,
    )

    await runtime.run("处理任务", event_handler=events)

    failed = [
        e for e in events.events if e.type is AgentEventType.SKILL_ACTIVATION_FAILED
    ]
    assert len(failed) == 1
    assert failed[0].skill_name == "big"
    assert "budget" in failed[0].skill_error
    assert not any(
        e.type is AgentEventType.SKILL_ACTIVATED for e in events.events
    )


def test_runtime_rejects_provider_without_store() -> None:
    registry = ModelAdapterRegistry(ModelSettings(_env_file=None))
    with pytest.raises(ValueError, match="skill_context_provider requires"):
        AgentRuntime(
            registry,
            ToolRegistry(),
            provider="fake",
            skill_context_provider=SkillContextProvider(
                max_tokens=4096,
                max_active=4,
            ),
        )
