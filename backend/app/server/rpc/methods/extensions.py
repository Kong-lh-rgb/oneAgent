"""Skill 与 MCP 扩展管理 RPC。

Renderer 只提交结构化字段；Host 负责生成 SKILL.md / mcp.json、执行正式领域校验
并原子写入。MCP 新配置不会在正在运行的 Host 中偷偷启动，保存后明确要求重启。
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.extensions import (
    ExtensionImportError,
    apply_import_plan,
    parse_import_plan,
)
from app.mcp import MCPConfigurationError, MCPServerConfig
from app.skills import SkillScope

from ..dispatcher import RpcContext, RpcDispatcher
from ..protocol import JsonRpcError, RpcErrorCode


async def extension_list(
    params: dict[str, Any],
    ctx: RpcContext,
) -> dict[str, Any]:
    del params
    skill_store = ctx.application.skill_store
    skills = (
        await skill_store.managed_catalog() if skill_store is not None else ()
    )
    diagnostics = skill_store.diagnostics() if skill_store is not None else ()

    config_store = ctx.application.mcp_config_store
    try:
        settings = await config_store.load()
        config_error = ctx.application.mcp_error
    except MCPConfigurationError as exc:
        settings = None
        config_error = str(exc)

    manager = ctx.application.mcp_manager
    statuses = {item.name: item for item in manager.statuses()} if manager else {}
    servers: list[dict[str, Any]] = []
    if settings is not None:
        for config in settings.servers:
            status = statuses.get(config.name)
            restart_required = config_store.restart_required(config.name)
            servers.append(
                {
                    "name": config.name,
                    "command": config.command,
                    "args": list(config.args),
                    "cwd": config.cwd,
                    "enabled": config.enabled,
                    "permission": config.permission.value,
                    # 只返回变量名，避免 Renderer 获得配置中的实际 secret 值。
                    "env_names": sorted(config.env),
                    "state": (
                        "restart_required"
                        if restart_required or status is None
                        else status.state.value
                    ),
                    "tool_names": list(status.tool_names) if status else [],
                    "error": status.error if status else None,
                }
            )

    return {
        "skills": [
            {
                "name": item.metadata.name,
                "description": item.metadata.description,
                "scope": item.metadata.scope.value,
                "location": str(item.metadata.location),
                "enabled": item.enabled,
            }
            for item in skills
        ],
        "skill_diagnostics": [
            {
                "name": item.name,
                "scope": item.scope.value,
                "location": item.location,
                "reason": item.reason,
            }
            for item in diagnostics
        ],
        "mcp": {
            "config_path": str(config_store.path),
            "error": config_error,
            "restart_required": config_store.has_pending_changes,
            "servers": servers,
        },
    }


async def extension_import_preview(
    params: dict[str, Any],
    ctx: RpcContext,
) -> dict[str, Any]:
    """只解析并返回导入计划；不联网、不写文件、不执行命令。"""

    del ctx
    try:
        scope = SkillScope(str(params.get("skill_scope", "project")))
        permission = _import_permission(params)
        plan = parse_import_plan(
            _require_str(params, "input"),
            skill_scope=scope,
            mcp_permission=permission,
        )
    except (ValueError, ExtensionImportError) as exc:
        raise JsonRpcError(RpcErrorCode.INVALID_PARAMS, str(exc)) from exc
    return {"plan": plan.public_dict()}


async def extension_import_apply(
    params: dict[str, Any],
    ctx: RpcContext,
) -> dict[str, Any]:
    """执行用户明确确认且指纹未变化的导入计划。"""

    if params.get("confirmed") is not True:
        raise JsonRpcError(
            RpcErrorCode.INVALID_PARAMS,
            "必须先预览并明确确认导入",
        )
    skill_store = ctx.application.skill_store
    mcp_store = ctx.application.mcp_config_store
    if skill_store is None or mcp_store is None:
        raise JsonRpcError(RpcErrorCode.INTERNAL_ERROR, "Extension Store unavailable")
    try:
        scope = SkillScope(str(params.get("skill_scope", "project")))
        permission = _import_permission(params)
        plan = parse_import_plan(
            _require_str(params, "input"),
            skill_scope=scope,
            mcp_permission=permission,
        )
        if plan.fingerprint != _require_str(params, "fingerprint"):
            raise ExtensionImportError("导入内容已变化，请重新生成预览")
        result = await apply_import_plan(
            plan,
            skill_store=skill_store,
            mcp_store=mcp_store,
        )
    except (
        ExtensionImportError,
        MCPConfigurationError,
        OSError,
        RuntimeError,
        ValueError,
    ) as exc:
        raise JsonRpcError(RpcErrorCode.INVALID_PARAMS, str(exc)) from exc
    return result


async def skill_install(
    params: dict[str, Any],
    ctx: RpcContext,
) -> dict[str, Any]:
    store = ctx.application.skill_store
    if store is None:
        raise JsonRpcError(RpcErrorCode.INTERNAL_ERROR, "Skill Store unavailable")
    name = _require_str(params, "name")
    description = _require_str(params, "description")
    instructions = _require_str(params, "instructions")
    try:
        scope = SkillScope(str(params.get("scope", SkillScope.PROJECT.value)))
        skill = await store.install(
            name=name,
            description=description,
            instructions=instructions,
            scope=scope,
        )
    except (ValueError, OSError) as exc:
        raise JsonRpcError(RpcErrorCode.INVALID_PARAMS, str(exc)) from exc
    return {
        "skill": {
            "name": skill.metadata.name,
            "description": skill.metadata.description,
            "scope": skill.metadata.scope.value,
            "location": str(skill.metadata.location),
            "enabled": True,
        }
    }


async def skill_set_enabled(
    params: dict[str, Any],
    ctx: RpcContext,
) -> dict[str, Any]:
    store = ctx.application.skill_store
    if store is None:
        raise JsonRpcError(RpcErrorCode.INTERNAL_ERROR, "Skill Store unavailable")
    try:
        entry = await store.set_enabled(
            name=_require_str(params, "name"),
            scope=SkillScope(_require_str(params, "scope")),
            enabled=_require_bool(params, "enabled"),
        )
    except (KeyError, ValueError, OSError, RuntimeError) as exc:
        raise JsonRpcError(RpcErrorCode.INVALID_PARAMS, str(exc)) from exc
    return {
        "skill": {
            "name": entry.metadata.name,
            "description": entry.metadata.description,
            "scope": entry.metadata.scope.value,
            "location": str(entry.metadata.location),
            "enabled": entry.enabled,
        }
    }


async def skill_delete(
    params: dict[str, Any],
    ctx: RpcContext,
) -> dict[str, Any]:
    store = ctx.application.skill_store
    if store is None:
        raise JsonRpcError(RpcErrorCode.INTERNAL_ERROR, "Skill Store unavailable")
    name = _require_str(params, "name")
    try:
        await store.delete(
            name=name,
            scope=SkillScope(_require_str(params, "scope")),
            enabled=_require_bool(params, "enabled"),
        )
    except (KeyError, ValueError, OSError) as exc:
        raise JsonRpcError(RpcErrorCode.INVALID_PARAMS, str(exc)) from exc
    return {"deleted": True, "name": name}


async def mcp_add(
    params: dict[str, Any],
    ctx: RpcContext,
) -> dict[str, Any]:
    try:
        server = MCPServerConfig.model_validate(
            {
                "name": params.get("name"),
                "transport": "stdio",
                "command": params.get("command"),
                "args": params.get("args", []),
                "env": params.get("env", {}),
                "cwd": params.get("cwd"),
                "enabled": params.get("enabled", True),
                "startup_timeout_seconds": params.get(
                    "startup_timeout_seconds", 15.0
                ),
                "call_timeout_seconds": params.get(
                    "call_timeout_seconds", 30.0
                ),
                "permission": params.get("permission", "human_approval"),
            }
        )
        await ctx.application.mcp_config_store.add(server)
    except (ValidationError, ValueError, MCPConfigurationError, OSError) as exc:
        raise JsonRpcError(RpcErrorCode.INVALID_PARAMS, str(exc)) from exc
    return {
        "server": {
            "name": server.name,
            "command": server.command,
            "args": list(server.args),
            "cwd": server.cwd,
            "enabled": server.enabled,
            "permission": server.permission.value,
            "env_names": sorted(server.env),
            "state": "restart_required",
            "tool_names": [],
            "error": None,
        },
        "restart_required": True,
        "config_path": str(ctx.application.mcp_config_store.path),
    }


async def mcp_set_enabled(
    params: dict[str, Any],
    ctx: RpcContext,
) -> dict[str, Any]:
    try:
        server = await ctx.application.mcp_config_store.set_enabled(
            _require_str(params, "name"),
            enabled=_require_bool(params, "enabled"),
        )
    except (KeyError, ValueError, MCPConfigurationError, OSError) as exc:
        raise JsonRpcError(RpcErrorCode.INVALID_PARAMS, str(exc)) from exc
    return {
        "server": _server_dict(server, state="restart_required"),
        "restart_required": True,
    }


async def mcp_delete(
    params: dict[str, Any],
    ctx: RpcContext,
) -> dict[str, Any]:
    name = _require_str(params, "name")
    try:
        await ctx.application.mcp_config_store.delete(name)
    except (KeyError, ValueError, MCPConfigurationError, OSError) as exc:
        raise JsonRpcError(RpcErrorCode.INVALID_PARAMS, str(exc)) from exc
    return {"deleted": True, "name": name, "restart_required": True}


def _require_str(params: dict[str, Any], key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        raise JsonRpcError(RpcErrorCode.INVALID_PARAMS, f"{key} is required")
    return value.strip()


def _require_bool(params: dict[str, Any], key: str) -> bool:
    value = params.get(key)
    if not isinstance(value, bool):
        raise JsonRpcError(RpcErrorCode.INVALID_PARAMS, f"{key} must be boolean")
    return value


def _import_permission(params: dict[str, Any]) -> str:
    value = str(params.get("mcp_permission", "human_approval"))
    if value not in {"allowed", "human_approval", "forbidden"}:
        raise ValueError("mcp_permission is invalid")
    return value


def _server_dict(
    server: MCPServerConfig,
    *,
    state: str,
) -> dict[str, Any]:
    return {
        "name": server.name,
        "command": server.command,
        "args": list(server.args),
        "cwd": server.cwd,
        "enabled": server.enabled,
        "permission": server.permission.value,
        "env_names": sorted(server.env),
        "state": state,
        "tool_names": [],
        "error": None,
    }


def register(dispatcher: RpcDispatcher) -> None:
    dispatcher.register("extension.list", extension_list)
    dispatcher.register("extension.import.preview", extension_import_preview)
    dispatcher.register("extension.import.apply", extension_import_apply)
    dispatcher.register("skill.install", skill_install)
    dispatcher.register("skill.set_enabled", skill_set_enabled)
    dispatcher.register("skill.delete", skill_delete)
    dispatcher.register("mcp.add", mcp_add)
    dispatcher.register("mcp.set_enabled", mcp_set_enabled)
    dispatcher.register("mcp.delete", mcp_delete)


__all__ = [
    "extension_list",
    "extension_import_apply",
    "extension_import_preview",
    "mcp_add",
    "mcp_delete",
    "mcp_set_enabled",
    "register",
    "skill_delete",
    "skill_install",
    "skill_set_enabled",
]
