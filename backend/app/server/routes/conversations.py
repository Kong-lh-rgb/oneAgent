"""Conversation API：列表 / 详情 / 创建 / 发送消息。

发送消息必须走 ``ConversationService.dispatch()``（统一 load → Run → 写回），
Server 不自己维护第二条执行链。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.conversation import ConversationSource, TriggerContext

from ..schemas import CreateConversationRequest, SendMessageRequest

router = APIRouter(tags=["conversations"])


@router.get("/conversations")
async def list_conversations(
    request: Request,
    limit: int = 50,
) -> dict[str, object]:
    application = request.app.state.application
    conversations = await application.conversation_store.list(limit=limit)
    return {"conversations": conversations, "count": len(conversations)}


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    request: Request,
) -> dict[str, object]:
    application = request.app.state.application
    conversation = await application.conversation_store.get(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    messages = await application.conversation_store.load_messages(conversation_id)
    return {"conversation": conversation, "messages": messages}


@router.post("/conversations")
async def create_conversation(
    body: CreateConversationRequest,
    request: Request,
) -> dict[str, object]:
    application = request.app.state.application
    conversation = await application.conversation_store.create(
        title=body.title or "新会话",
    )
    return {"conversation": conversation}


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: str,
    body: SendMessageRequest,
    request: Request,
) -> dict[str, object]:
    application = request.app.state.application
    conversation = await application.conversation_store.get(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="conversation not found")

    dispatch = await application.conversation_service.dispatch(
        conversation_id=conversation_id,
        content=body.content,
        trigger=TriggerContext(source=ConversationSource.MANUAL),
    )
    # 新会话第一次手动发送 → 复用现有标题生成逻辑（不另造 title agent）。
    if conversation.title == "新会话":
        await application.conversation_store.rename(
            conversation_id,
            _title_from_content(body.content),
        )
    return {
        "conversation_id": conversation_id,
        "run": dispatch.run,
        "result": dispatch.result,
        "content": dispatch.result.content,
    }


def _title_from_content(content: str) -> str:
    from app.application import title_from_content

    return title_from_content(content)


__all__ = ["router"]
