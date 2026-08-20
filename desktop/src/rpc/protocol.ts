/** JSON-RPC 2.0 协议消息类型与编解码（V0 子集：request/response/error/notification）。 */

export interface RpcRequestMessage {
  jsonrpc: '2.0'
  id: number
  method: string
  params?: Record<string, unknown>
}

export interface RpcResponseMessage {
  jsonrpc: '2.0'
  id: number
  result?: unknown
  error?: { code: number; message: string; data?: unknown }
}

export interface RpcNotificationMessage {
  jsonrpc: '2.0'
  method: string
  params?: unknown
}

export type RpcIncomingMessage = RpcResponseMessage | RpcNotificationMessage

export function encodeRequest(
  id: number,
  method: string,
  params?: Record<string, unknown>,
): string {
  return JSON.stringify({
    jsonrpc: '2.0',
    id,
    method,
    params: params ?? {},
  })
}

export function parseMessage(text: string): RpcIncomingMessage {
  return JSON.parse(text) as RpcIncomingMessage
}
