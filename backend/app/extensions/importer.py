"""GitHub Skill 与外部 MCP JSON 的安全导入服务。

预览阶段只解析文本，不访问网络、不启动子进程。确认阶段仅允许从 GitHub 下载
静态 Skill 文件，或者把经过领域模型校验的 MCP 配置写入本地配置文件。
"""

from __future__ import annotations

import hashlib
import html
import io
import json
import re
import shlex
import stat
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

import httpx

from app.mcp import MCPServerConfig
from app.skills import SkillParseError, SkillScope, parse_skill_document

if TYPE_CHECKING:
    from app.mcp import MCPConfigurationStore
    from app.skills import SkillStore

_GITHUB_REPOSITORY_RE = re.compile(
    r"^(?:https://github\.com/)?"
    r"(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,38}))"
    r"/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)
_MCP_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
_MAX_IMPORT_TEXT_CHARS = 200_000
_MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
_MAX_SKILL_PACKAGE_BYTES = 10 * 1024 * 1024
_RESOURCE_DIRS = frozenset({"scripts", "references", "assets"})


class ExtensionImportError(ValueError):
    """外部扩展无法安全解析或安装。"""


@dataclass(frozen=True)
class GitHubSkillSource:
    """一个经格式校验的 GitHub Skill 仓库来源。"""

    owner: str
    repository: str
    scope: SkillScope

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repository}"

    @property
    def url(self) -> str:
        return f"https://github.com/{self.slug}"


@dataclass(frozen=True)
class ExtensionImportPlan:
    """预览和确认阶段共用的不可变规范化计划。"""

    fingerprint: str
    raw_input: str
    skill_sources: tuple[GitHubSkillSource, ...]
    mcp_servers: tuple[MCPServerConfig, ...]
    warnings: tuple[str, ...]

    def public_dict(self) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        actions: list[str] = []
        for source in self.skill_sources:
            items.append(
                {
                    "kind": "skill",
                    "name": source.repository,
                    "source": source.url,
                    "scope": source.scope.value,
                    "summary": "确认后下载仓库并安装其中通过校验的 Skill 包",
                }
            )
            actions.extend(
                (
                    f"下载 {source.url} 的静态仓库归档",
                    "检查 SKILL.md，并只复制 scripts / references / assets",
                    "不会执行 npx、git、安装脚本或仓库中的代码",
                )
            )
        for server in self.mcp_servers:
            command = shlex.join((server.command, *server.args))
            items.append(
                {
                    "kind": "mcp",
                    "name": server.name,
                    "source": "外部 MCP 配置",
                    "summary": f"写入 stdio Server；重启 Host 后运行 {command}",
                    "command": server.command,
                    "args": list(server.args),
                    "cwd": server.cwd,
                    "env_names": sorted(server.env),
                    "permission": server.permission.value,
                }
            )
            actions.append(f"写入 MCP {server.name}：{command}")
        return {
            "fingerprint": self.fingerprint,
            "items": items,
            "actions": actions,
            "warnings": list(self.warnings),
            "requires_download": bool(self.skill_sources),
            "requires_restart": bool(self.mcp_servers),
        }


def parse_import_plan(
    raw_input: str,
    *,
    skill_scope: SkillScope = SkillScope.PROJECT,
    mcp_permission: str = "human_approval",
) -> ExtensionImportPlan:
    """解析外部格式；本函数保证不联网，也不执行输入中的命令。"""

    cleaned = html.unescape(raw_input).strip()
    if not cleaned:
        raise ExtensionImportError("请粘贴 GitHub 地址、owner/repo 或 MCP JSON")
    if len(cleaned) > _MAX_IMPORT_TEXT_CHARS:
        raise ExtensionImportError("导入内容过大")

    skill_sources: list[GitHubSkillSource] = []
    mcp_servers: list[MCPServerConfig] = []
    warnings: list[str] = []
    payload = _try_json(cleaned)
    if payload is None:
        source = _parse_skill_source_or_command(cleaned, skill_scope)
        skill_sources.append(source)
    else:
        raw_servers = _external_servers(payload)
        for external_name, raw_server in raw_servers:
            command, args = _command_and_args(raw_server)
            skill_slug = _skill_add_source(command, args)
            if skill_slug is not None:
                skill_sources.append(
                    _parse_github_source(skill_slug, skill_scope)
                )
                continue
            normalized_name = _normalize_mcp_name(external_name)
            if normalized_name != external_name:
                warnings.append(
                    f"MCP 名称 {external_name!r} 已转换为 {normalized_name!r}"
                )
            env = _string_mapping(raw_server.get("env", {}), "env")
            if any(not _is_env_reference(value) for value in env.values()):
                warnings.append(
                    f"MCP {normalized_name} 含直接环境变量值；"
                    "建议改为 ${ENV_NAME} 引用"
                )
            try:
                mcp_servers.append(
                    MCPServerConfig.model_validate(
                        {
                            "name": normalized_name,
                            "transport": "stdio",
                            "command": command,
                            "args": args,
                            "env": env,
                            "cwd": raw_server.get("cwd"),
                            "enabled": raw_server.get("enabled", True),
                            "startup_timeout_seconds": raw_server.get(
                                "startup_timeout_seconds", 15.0
                            ),
                            "call_timeout_seconds": raw_server.get(
                                "call_timeout_seconds", 30.0
                            ),
                            "permission": raw_server.get(
                                "permission", mcp_permission
                            ),
                        }
                    )
                )
            except (TypeError, ValueError) as exc:
                raise ExtensionImportError(
                    f"MCP {external_name!r} 配置无效：{exc}"
                ) from exc

    _ensure_unique(skill_sources, mcp_servers)
    fingerprint = _fingerprint(cleaned, skill_scope, mcp_permission)
    return ExtensionImportPlan(
        fingerprint=fingerprint,
        raw_input=cleaned,
        skill_sources=tuple(skill_sources),
        mcp_servers=tuple(mcp_servers),
        warnings=tuple(dict.fromkeys(warnings)),
    )


async def apply_import_plan(
    plan: ExtensionImportPlan,
    *,
    skill_store: SkillStore,
    mcp_store: MCPConfigurationStore,
) -> dict[str, Any]:
    """执行已确认计划；只下载静态 Skill 归档并写入正式 Store。"""

    packages: list[tuple[GitHubSkillSource, dict[str, bytes]]] = []
    for source in plan.skill_sources:
        archive = await _download_github_archive(source)
        discovered = _skill_packages_from_archive(archive)
        if not discovered:
            raise ExtensionImportError(
                f"{source.slug} 中没有找到可由 Vesta 加载的 SKILL.md"
            )
        packages.extend((source, package) for package in discovered)

    installed_skills: list[dict[str, str]] = []
    for source, package in packages:
        skill = await skill_store.install_package(
            files=package,
            scope=source.scope,
        )
        installed_skills.append(
            {
                "name": skill.metadata.name,
                "scope": skill.metadata.scope.value,
                "source": source.url,
            }
        )

    if plan.mcp_servers:
        await mcp_store.add_many(plan.mcp_servers)

    return {
        "skills": installed_skills,
        "mcp_servers": [server.name for server in plan.mcp_servers],
        "restart_required": bool(plan.mcp_servers),
    }


def _try_json(value: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        raise ExtensionImportError("MCP JSON 顶层必须是对象")
    return payload


def _external_servers(
    payload: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    if "mcpServers" in payload:
        servers = payload["mcpServers"]
        if not isinstance(servers, dict) or not servers:
            raise ExtensionImportError("mcpServers 必须是非空对象")
        result: list[tuple[str, dict[str, Any]]] = []
        for name, config in servers.items():
            if not isinstance(name, str) or not isinstance(config, dict):
                raise ExtensionImportError("mcpServers 中的名称和配置必须有效")
            result.append((name, config))
        return result
    if "servers" in payload:
        servers = payload["servers"]
        if not isinstance(servers, list) or not servers:
            raise ExtensionImportError("servers 必须是非空数组")
        result = []
        for config in servers:
            if not isinstance(config, dict):
                raise ExtensionImportError("servers 中的配置必须是对象")
            name = config.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ExtensionImportError("servers 中的每项都必须包含 name")
            result.append((name.strip(), config))
        return result
    raise ExtensionImportError("未找到 mcpServers 或 servers")


def _command_and_args(config: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    command = config.get("command")
    args = config.get("args", [])
    if not isinstance(command, str) or not command.strip():
        raise ExtensionImportError("MCP command 必须是非空字符串")
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        raise ExtensionImportError("MCP args 必须是字符串数组")
    return command.strip(), tuple(item for item in args if item)


def _parse_skill_source_or_command(
    value: str,
    scope: SkillScope,
) -> GitHubSkillSource:
    try:
        parts = shlex.split(value)
    except ValueError as exc:
        raise ExtensionImportError(f"无法解析输入：{exc}") from exc
    if parts:
        source = _skill_add_source(parts[0], tuple(parts[1:]))
        if source is not None:
            return _parse_github_source(source, scope)
    return _parse_github_source(value, scope)


def _skill_add_source(command: str, args: tuple[str, ...]) -> str | None:
    executable = PurePosixPath(command).name.lower()
    if executable not in {"npx", "npm", "pnpm", "yarn", "bunx"}:
        return None
    lowered = [item.lower() for item in args]
    for index in range(len(lowered) - 2):
        if lowered[index] == "skills" and lowered[index + 1] == "add":
            return args[index + 2]
    return None


def _parse_github_source(value: str, scope: SkillScope) -> GitHubSkillSource:
    match = _GITHUB_REPOSITORY_RE.fullmatch(value.strip())
    if match is None:
        raise ExtensionImportError(
            "GitHub 来源必须是 https://github.com/owner/repo 或 owner/repo"
        )
    return GitHubSkillSource(
        owner=match.group("owner"),
        repository=match.group("repo"),
        scope=scope,
    )


def _normalize_mcp_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_]", "_", value.strip())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    if not normalized or _MCP_NAME_RE.fullmatch(normalized) is None:
        raise ExtensionImportError(f"无法把 MCP 名称 {value!r} 转成安全名称")
    return normalized


def _string_mapping(value: object, label: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ExtensionImportError(f"{label} 必须是对象")
    if not all(
        isinstance(key, str) and isinstance(item, str)
        for key, item in value.items()
    ):
        raise ExtensionImportError(f"{label} 的名称和值都必须是字符串")
    return dict(value)


def _is_env_reference(value: str) -> bool:
    return re.fullmatch(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}", value) is not None


def _ensure_unique(
    skill_sources: list[GitHubSkillSource],
    mcp_servers: list[MCPServerConfig],
) -> None:
    skill_slugs = [item.slug.lower() for item in skill_sources]
    server_names = [item.name for item in mcp_servers]
    if len(skill_slugs) != len(set(skill_slugs)):
        raise ExtensionImportError("导入内容包含重复的 Skill 来源")
    if len(server_names) != len(set(server_names)):
        raise ExtensionImportError("名称转换后产生了重复 MCP Server")
    if not skill_sources and not mcp_servers:
        raise ExtensionImportError("没有识别到可导入的扩展")


def _fingerprint(raw: str, scope: SkillScope, permission: str) -> str:
    material = f"v1\0{scope.value}\0{permission}\0{raw}".encode()
    return hashlib.sha256(material).hexdigest()


async def _download_github_archive(source: GitHubSkillSource) -> bytes:
    url = f"https://api.github.com/repos/{source.slug}/zipball"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "Vesta"}
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(45.0),
        ) as client:
            async with client.stream("GET", url, headers=headers) as response:
                response.raise_for_status()
                declared_size = response.headers.get("content-length")
                try:
                    declared_bytes = int(declared_size) if declared_size else 0
                except ValueError:
                    declared_bytes = 0
                if declared_bytes > _MAX_ARCHIVE_BYTES:
                    raise ExtensionImportError(
                        f"{source.slug} 归档超过 "
                        f"{_MAX_ARCHIVE_BYTES // 1024 // 1024}MB 限制"
                    )
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > _MAX_ARCHIVE_BYTES:
                        raise ExtensionImportError(
                            f"{source.slug} 归档超过 "
                            f"{_MAX_ARCHIVE_BYTES // 1024 // 1024}MB 限制"
                        )
    except httpx.HTTPError as exc:
        raise ExtensionImportError(
            f"下载 {source.slug} 失败：{type(exc).__name__}: {exc}"
        ) from exc
    return bytes(chunks)


def _skill_packages_from_archive(archive: bytes) -> list[dict[str, bytes]]:
    try:
        bundle = zipfile.ZipFile(io.BytesIO(archive))
    except (OSError, zipfile.BadZipFile) as exc:
        raise ExtensionImportError("GitHub 返回的不是有效 ZIP 归档") from exc

    files: dict[PurePosixPath, bytes] = {}
    total = 0
    for info in bundle.infolist():
        path = PurePosixPath(info.filename)
        if info.is_dir():
            continue
        if path.is_absolute() or ".." in path.parts:
            raise ExtensionImportError("Skill 归档包含越界路径")
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            continue
        total += info.file_size
        if total > _MAX_SKILL_PACKAGE_BYTES:
            raise ExtensionImportError("Skill 文件超过 10MB 安全限制")
        files[path] = bundle.read(info)

    packages: list[dict[str, bytes]] = []
    for path, content in files.items():
        if path.name != "SKILL.md":
            continue
        skill_name = path.parent.name
        try:
            document = content.decode("utf-8")
            parse_skill_document(document, expected_name=skill_name)
        except (UnicodeError, SkillParseError):
            continue
        package: dict[str, bytes] = {"SKILL.md": content}
        for candidate, candidate_content in files.items():
            try:
                relative = candidate.relative_to(path.parent)
            except ValueError:
                continue
            if len(relative.parts) >= 2 and relative.parts[0] in _RESOURCE_DIRS:
                package[relative.as_posix()] = candidate_content
        packages.append(package)
    return packages


__all__ = [
    "ExtensionImportError",
    "ExtensionImportPlan",
    "apply_import_plan",
    "parse_import_plan",
]
