/** 共享 RpcClient 单例 + 便捷 typed methods / notification 类型。 */

import { WS_URL } from '../api/config'
import { RpcClient } from './client'

/** Renderer 全局唯一的 RPC 连接（一条 WS /rpc，双向通信）。 */
export const rpcClient = new RpcClient({
  url: `${WS_URL}/rpc`,
})

export * from './client'
export * from './errors'
export * from './protocol'
export type {
  AgentEventNotificationParams,
  ApprovalNotificationParams,
  RunStatusNotificationParams,
} from './notifications'
