/** Conversation API 客户端。 */

import { apiGet, apiPost } from './http'
import type { Conversation, Message, SendMessageResponse } from './types'

export async function listConversations(limit = 50): Promise<Conversation[]> {
  const data = await apiGet<{ conversations: Conversation[] }>(
    `/api/conversations?limit=${limit}`,
  )
  return data.conversations
}

export async function getConversation(
  conversationId: string,
): Promise<{ conversation: Conversation; messages: Message[] }> {
  return apiGet<{ conversation: Conversation; messages: Message[] }>(
    `/api/conversations/${conversationId}`,
  )
}

export async function createConversation(): Promise<Conversation> {
  const data = await apiPost<{ conversation: Conversation }>('/api/conversations', {})
  return data.conversation
}

export async function sendMessage(
  conversationId: string,
  content: string,
): Promise<SendMessageResponse> {
  return apiPost<SendMessageResponse>(
    `/api/conversations/${conversationId}/messages`,
    { content },
  )
}
