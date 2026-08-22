"""Desktop 扩展管理：Skill 安装与 MCP JSON 写入离线测试。"""

from __future__ import annotations

import io
import json
import zipfile
from types import SimpleNamespace

import pytest

from app.extensions import (
    ExtensionImportError,
    apply_import_plan,
    importer,
    parse_import_plan,
)
from app.mcp import MCPConfigurationStore, MCPServerConfig
from app.server.rpc.dispatcher import RpcContext
from app.server.rpc.methods import extensions
from app.server.rpc.protocol import JsonRpcError
from app.skills import SkillScope, SkillStore


@pytest.mark.asyncio
async def test_skill_install_generates_valid_markdown_and_catalog(tmp_path) -> None:
    store = SkillStore(
        user_dir=tmp_path / "user",
        project_dir=tmp_path / "project",
    )
    await store.initialize()

    installed = await store.install(
        name="release-check",
        description="发布前检查：冒号也应被正确转义",
        instructions="# Release Check\n\n1. 运行测试。\n2. 检查差异。",
        scope=SkillScope.PROJECT,
    )

    assert installed.metadata.name == "release-check"
    assert installed.metadata.scope is SkillScope.PROJECT
    text = (tmp_path / "project/release-check/SKILL.md").read_text("utf-8")
    assert "name: release-check" in text
    assert "# Release Check" in text
    assert [item.name for item in await store.catalog()] == ["release-check"]


@pytest.mark.asyncio
async def test_skill_install_rejects_duplicate_without_changing_file(tmp_path) -> None:
    store = SkillStore(
        user_dir=tmp_path / "user",
        project_dir=tmp_path / "project",
    )
    await store.initialize()
    await store.install(
        name="demo",
        description="原描述",
        instructions="原指令",
    )
    path = tmp_path / "project/demo/SKILL.md"
    before = path.read_bytes()

    with pytest.raises(ValueError, match="already exists"):
        await store.install(
            name="demo",
            description="新描述",
            instructions="新指令",
        )

    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_skill_disable_enable_and_delete_are_scope_safe(tmp_path) -> None:
    store = SkillStore(
        user_dir=tmp_path / "user",
        project_dir=tmp_path / "project",
    )
    await store.initialize()
    await store.install(
        name="demo",
        description="演示",
        instructions="演示步骤",
        scope=SkillScope.PROJECT,
    )

    disabled = await store.set_enabled(
        name="demo",
        scope=SkillScope.PROJECT,
        enabled=False,
    )
    assert disabled.enabled is False
    assert await store.catalog() == ()
    assert (tmp_path / "project/.disabled/demo/SKILL.md").is_file()
    managed = await store.managed_catalog()
    assert [(item.metadata.name, item.enabled) for item in managed] == [
        ("demo", False),
    ]
    assert store.diagnostics() == ()

    enabled = await store.set_enabled(
        name="demo",
        scope=SkillScope.PROJECT,
        enabled=True,
    )
    assert enabled.enabled is True
    assert [item.name for item in await store.catalog()] == ["demo"]

    await store.delete(
        name="demo",
        scope=SkillScope.PROJECT,
        enabled=True,
    )
    assert await store.managed_catalog() == ()


@pytest.mark.asyncio
async def test_mcp_configuration_store_writes_valid_json_and_preserves_entries(
    tmp_path,
) -> None:
    path = tmp_path / "mcp.json"
    store = MCPConfigurationStore(path)
    await store.add(
        MCPServerConfig(
            name="filesystem",
            command="npx",
            args=("-y", "@modelcontextprotocol/server-filesystem", "/tmp/work"),
        )
    )
    await store.add(
        MCPServerConfig(
            name="weather",
            command="uvx",
            args=("weather-mcp",),
            env={"WEATHER_API_KEY": "${WEATHER_API_KEY}"},
            permission="allowed",
        )
    )

    payload = json.loads(path.read_text("utf-8"))
    assert [item["name"] for item in payload["servers"]] == [
        "filesystem",
        "weather",
    ]
    assert payload["servers"][1]["args"] == ["weather-mcp"]
    assert payload["servers"][1]["env"] == {
        "WEATHER_API_KEY": "${WEATHER_API_KEY}"
    }


@pytest.mark.asyncio
async def test_mcp_configuration_store_disable_enable_and_delete(tmp_path) -> None:
    store = MCPConfigurationStore(tmp_path / "mcp.json")
    await store.add(MCPServerConfig(name="demo", command="server"))

    disabled = await store.set_enabled("demo", enabled=False)
    assert disabled.enabled is False
    assert (await store.load()).servers[0].enabled is False
    assert store.restart_required("demo") is True

    enabled = await store.set_enabled("demo", enabled=True)
    assert enabled.enabled is True
    await store.delete("demo")
    assert (await store.load()).servers == ()


@pytest.mark.asyncio
async def test_extension_list_never_returns_mcp_secret_values(tmp_path) -> None:
    skill_store = SkillStore(
        user_dir=tmp_path / "user",
        project_dir=tmp_path / "project",
    )
    await skill_store.initialize()
    config_store = MCPConfigurationStore(tmp_path / "mcp.json")
    await config_store.add(
        MCPServerConfig(
            name="private",
            command="server",
            env={"API_KEY": "real-secret"},
        )
    )
    app = SimpleNamespace(
        skill_store=skill_store,
        mcp_config_store=config_store,
        mcp_manager=None,
        mcp_error=None,
    )
    ctx = RpcContext(app, SimpleNamespace())

    result = await extensions.extension_list({}, ctx)

    server = result["mcp"]["servers"][0]
    assert server["env_names"] == ["API_KEY"]
    assert "real-secret" not in json.dumps(result)
    assert server["state"] == "restart_required"


@pytest.mark.asyncio
async def test_mcp_add_rejects_duplicate_and_keeps_json_unchanged(tmp_path) -> None:
    config_store = MCPConfigurationStore(tmp_path / "mcp.json")
    await config_store.add(MCPServerConfig(name="demo", command="server"))
    before = config_store.path.read_bytes()
    app = SimpleNamespace(mcp_config_store=config_store)
    ctx = RpcContext(app, SimpleNamespace())

    with pytest.raises(JsonRpcError):
        await extensions.mcp_add(
            {"name": "demo", "command": "another-server"},
            ctx,
        )

    assert config_store.path.read_bytes() == before


def test_import_preview_recognizes_skill_installer_without_executing_it() -> None:
    plan = parse_import_plan(
        json.dumps(
            {
                "mcpServers": {
                    "gpt-researcher": {
                        "command": "npx",
                        "args": [
                            "skills",
                            "add",
                            "assafelovic/gpt-researcher",
                        ],
                    }
                }
            }
        )
    )

    assert [item.slug for item in plan.skill_sources] == [
        "assafelovic/gpt-researcher"
    ]
    assert plan.mcp_servers == ()
    public = plan.public_dict()
    assert public["items"][0]["kind"] == "skill"
    assert any("不会执行 npx" in action for action in public["actions"])


def test_import_preview_converts_external_mcp_json() -> None:
    plan = parse_import_plan(
        json.dumps(
            {
                "mcpServers": {
                    "weather-server": {
                        "command": "uvx",
                        "args": ["weather-mcp"],
                        "env": {"API_KEY": "${API_KEY}"},
                    }
                }
            }
        )
    )

    server = plan.mcp_servers[0]
    assert server.name == "weather_server"
    assert server.command == "uvx"
    assert server.args == ("weather-mcp",)
    assert plan.public_dict()["items"][0]["env_names"] == ["API_KEY"]
    assert "${API_KEY}" not in json.dumps(plan.public_dict())


@pytest.mark.asyncio
async def test_import_apply_downloads_static_skill_without_running_command(
    tmp_path,
    monkeypatch,
) -> None:
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w") as archive:
        archive.writestr(
            "owner-repo/skills/research/SKILL.md",
            "---\nname: research\ndescription: 研究流程\n---\n\n# Steps\n\n1. Search.",
        )
        archive.writestr(
            "owner-repo/skills/research/references/checklist.md",
            "verify sources",
        )
        archive.writestr("owner-repo/package.json", "{}")

    async def fake_download(_source) -> bytes:
        return archive_buffer.getvalue()

    monkeypatch.setattr(importer, "_download_github_archive", fake_download)
    skill_store = SkillStore(
        user_dir=tmp_path / "user",
        project_dir=tmp_path / "project",
    )
    await skill_store.initialize()
    mcp_store = MCPConfigurationStore(tmp_path / "mcp.json")
    plan = parse_import_plan("owner/repo")

    result = await apply_import_plan(
        plan,
        skill_store=skill_store,
        mcp_store=mcp_store,
    )

    assert result["skills"][0]["name"] == "research"
    assert (tmp_path / "project/research/SKILL.md").is_file()
    assert (
        tmp_path / "project/research/references/checklist.md"
    ).read_text("utf-8") == "verify sources"
    assert not (tmp_path / "project/research/package.json").exists()


def test_import_rejects_zip_path_traversal() -> None:
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w") as archive:
        archive.writestr("owner-repo/../outside.txt", "unsafe")

    with pytest.raises(ExtensionImportError, match="越界路径"):
        importer._skill_packages_from_archive(archive_buffer.getvalue())


@pytest.mark.asyncio
async def test_import_apply_requires_confirmation_and_matching_preview(
    tmp_path,
) -> None:
    skill_store = SkillStore(
        user_dir=tmp_path / "user",
        project_dir=tmp_path / "project",
    )
    await skill_store.initialize()
    app = SimpleNamespace(
        skill_store=skill_store,
        mcp_config_store=MCPConfigurationStore(tmp_path / "mcp.json"),
    )
    ctx = RpcContext(app, SimpleNamespace())
    raw = json.dumps(
        {"mcpServers": {"demo": {"command": "uvx", "args": ["demo"]}}}
    )
    preview = await extensions.extension_import_preview({"input": raw}, ctx)

    with pytest.raises(JsonRpcError, match="明确确认"):
        await extensions.extension_import_apply({"input": raw}, ctx)
    with pytest.raises(JsonRpcError, match="重新生成预览"):
        await extensions.extension_import_apply(
            {
                "input": raw + " ",
                "fingerprint": "wrong",
                "confirmed": True,
            },
            ctx,
        )

    result = await extensions.extension_import_apply(
        {
            "input": raw,
            "fingerprint": preview["plan"]["fingerprint"],
            "confirmed": True,
        },
        ctx,
    )
    assert result["mcp_servers"] == ["demo"]
