/** Conversation API：全部走共享 JSON-RPC WebSocket。 */

import { rpcClient } from '../rpc'
import { RpcMethods } from '../rpc/methods'
import type {
  AgentMode,
  Conversation,
  Message,
  SendMessageResponse,
} from './types'

export async function listConversations(limit = 50): Promise<Conversation[]> {
  const data = await rpcClient.call<{ conversations: Conversation[] }>(
    RpcMethods.conversationList,
    { limit },
  )
  return data.conversations
}

export async function getConversation(
  conversationId: string,
): Promise<{ conversation: Conversation; messages: Message[] }> {
  return rpcClient.call(RpcMethods.conversationGet, {
    conversation_id: conversationId,
  })
}

export async function createConversation(): Promise<Conversation> {
  const data = await rpcClient.call<{ conversation: Conversation }>(
    RpcMethods.conversationCreate,
    {},
  )
  return data.conversation
}

export async function sendMessage(
  conversationId: string,
  content: string,
  mode: AgentMode = 'normal',
): Promise<SendMessageResponse> {
  return rpcClient.call(RpcMethods.conversationSend, {
    conversation_id: conversationId,
    content,
    mode,
  })
}
