"""conversation RPC methods。

conversation.send 必须继续走 ``ConversationService.dispatch``（统一
load → Run → 写回），RPC 层禁止自己复制执行链。
"""

from __future__ import annotations

from typing import Any

from app.application import title_from_content
from app.conversation import ConversationSource, TriggerContext

from ..dispatcher import RpcContext, RpcDispatcher
from ..protocol import RESOURCE_NOT_FOUND, JsonRpcError, RpcErrorCode


async def conversation_list(
    params: dict[str, Any],
    ctx: RpcContext,
) -> dict[str, Any]:
    limit = _positive_int(params, "limit", default=50)
    conversations = await ctx.application.conversation_store.list(limit=limit)
    return {"conversations": conversations, "count": len(conversations)}


async def conversation_get(
    params: dict[str, Any],
    ctx: RpcContext,
) -> dict[str, Any]:
    conversation_id = _require_str(params, "conversation_id")
    application = ctx.application
    conversation = await application.conversation_store.get(conversation_id)
    if conversation is None:
        raise JsonRpcError(RESOURCE_NOT_FOUND, "conversation not found")
    messages = await application.conversation_store.load_messages(conversation_id)
    return {"conversation": conversation, "messages": messages}


async def conversation_create(
    params: dict[str, Any],
    ctx: RpcContext,
) -> dict[str, Any]:
    title = params.get("title")
    if title is not None and (not isinstance(title, str) or not title.strip()):
        raise JsonRpcError(RpcErrorCode.INVALID_PARAMS, "title must be a string")
    conversation = await ctx.application.conversation_store.create(
        title=title or "新会话"
    )
    return {"conversation": conversation}


async def conversation_send(
    params: dict[str, Any],
    ctx: RpcContext,
) -> dict[str, Any]:
    conversation_id = _require_str(params, "conversation_id")
    content = _require_str(params, "content")
    if not content.strip():
        raise JsonRpcError(RpcErrorCode.INVALID_PARAMS, "content must be non-empty")

    application = ctx.application
    conversation = await application.conversation_store.get(conversation_id)
    if conversation is None:
        raise JsonRpcError(RESOURCE_NOT_FOUND, "conversation not found")

    # 统一执行入口：禁止 RPC 层自己 load history → start → save。
    dispatch = await application.conversation_service.dispatch(
        conversation_id=conversation_id,
        content=content,
        trigger=TriggerContext(source=ConversationSource.MANUAL),
    )
    # 新会话第一次手动发送 → 复用现有标题生成逻辑。
    if conversation.title == "新会话":
        await application.conversation_store.rename(
            conversation_id,
            title_from_content(content),
        )
    return {
        "conversation_id": conversation_id,
        "run": dispatch.run,
        "result": dispatch.result,
        "content": dispatch.result.content,
    }


def _require_str(params: dict[str, Any], key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value:
        raise JsonRpcError(RpcErrorCode.INVALID_PARAMS, f"{key} is required")
    return value


def _positive_int(params: dict[str, Any], key: str, *, default: int) -> int:
    value = params.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise JsonRpcError(
            RpcErrorCode.INVALID_PARAMS,
            f"{key} must be a positive integer",
        )
    return value


def register(dispatcher: RpcDispatcher) -> None:
    dispatcher.register("conversation.list", conversation_list)
    dispatcher.register("conversation.get", conversation_get)
    dispatcher.register("conversation.create", conversation_create)
    dispatcher.register("conversation.send", conversation_send)
