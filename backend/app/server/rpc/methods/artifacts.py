"""artifact RPC methods：list / get（全部走 WS /rpc，无 REST CRUD）。"""

from __future__ import annotations

import re
from typing import Any

from ..dispatcher import RpcContext, RpcDispatcher
from ..protocol import RESOURCE_NOT_FOUND, JsonRpcError, RpcErrorCode

_ARTIFACT_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_MAX_LIST_LIMIT = 200


async def artifact_list(
    params: dict[str, Any], ctx: RpcContext
) -> dict[str, Any]:
    run_id = params.get("run_id")
    conversation_id = params.get("conversation_id")
    if run_id is not None and (not isinstance(run_id, str) or not run_id):
        raise JsonRpcError(RpcErrorCode.INVALID_PARAMS, "run_id must be a string")
    if conversation_id is not None and (
        not isinstance(conversation_id, str) or not conversation_id
    ):
        raise JsonRpcError(
            RpcErrorCode.INVALID_PARAMS, "conversation_id must be a string"
        )
    limit = params.get("limit")
    if limit is None:
        limit = 50
    if (
        not isinstance(limit, int)
        or isinstance(limit, bool)
        or limit <= 0
        or limit > _MAX_LIST_LIMIT
    ):
        raise JsonRpcError(
            RpcErrorCode.INVALID_PARAMS,
            f"limit must be an int between 1 and {_MAX_LIST_LIMIT}",
        )

    store = ctx.application.artifact_store
    if store is None:
        return {"artifacts": [], "count": 0}
    artifacts = await store.list(
        run_id=run_id, conversation_id=conversation_id, limit=limit
    )
    payload = [artifact.public_dict() for artifact in artifacts]
    return {"artifacts": payload, "count": len(payload)}


async def artifact_get(
    params: dict[str, Any], ctx: RpcContext
) -> dict[str, Any]:
    artifact_id = params.get("id")
    if not isinstance(artifact_id, str) or not _ARTIFACT_ID_RE.fullmatch(
        artifact_id
    ):
        raise JsonRpcError(RpcErrorCode.INVALID_PARAMS, "id is required")

    store = ctx.application.artifact_store
    if store is None:
        raise JsonRpcError(RESOURCE_NOT_FOUND, "artifact not found")
    artifact = await store.get(artifact_id)
    if artifact is None:
        raise JsonRpcError(RESOURCE_NOT_FOUND, "artifact not found")
    return {"artifact": artifact.public_dict()}


def register(dispatcher: RpcDispatcher) -> None:
    dispatcher.register("artifact.list", artifact_list)
    dispatcher.register("artifact.get", artifact_get)
