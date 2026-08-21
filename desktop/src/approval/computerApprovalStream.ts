/** 独立电脑审批 Renderer 的 RPC 生命周期与事件绑定。 */

import type { AgentEvent, ApprovalRequest } from '../api/types'

type NotificationHandler = (params: unknown) => void

export interface ApprovalStreamClient {
  readonly connected: boolean
  connect: () => void
  disconnect: () => void
  on: (method: string, handler: NotificationHandler) => () => void
  setStatusListener: (listener: (connected: boolean) => void) => () => void
}

export interface ApprovalStreamCallbacks {
  syncPending: () => Promise<void>
  onRequired: (approval: ApprovalRequest) => void
  onResolved: (approval: ApprovalRequest) => void
  onAgentEvent: (event: AgentEvent) => void
  onRunStatus: (runId: string, status: string) => void
  onConnectionError?: (error: unknown) => void
}

/**
 * 建立浮窗专属 RPC 连接，并在首次连接和每次重连后重新同步 pending 审批。
 *
 * Electron 的每个 Renderer 都有独立的 JavaScript 上下文，因此主聊天窗口的
 * RpcClient 单例无法替浮窗收取事件；浮窗必须拥有自己的连接生命周期。
 */
export function connectComputerApprovalStream(
  client: ApprovalStreamClient,
  callbacks: ApprovalStreamCallbacks,
): () => void {
  let disposed = false

  const syncPending = (): void => {
    void callbacks.syncPending().catch((error: unknown) => {
      if (!disposed) callbacks.onConnectionError?.(error)
    })
  }

  // 先订阅，再连接，避免 socket 建立后审批事件先于 handler 到达。
  const offRequired = client.on('approval.required', (params) => {
    const approval = (params as { approval?: ApprovalRequest })?.approval
    if (approval) callbacks.onRequired(approval)
  })
  const offResolved = client.on('approval.resolved', (params) => {
    const approval = (params as { approval?: ApprovalRequest })?.approval
    if (approval) callbacks.onResolved(approval)
  })
  const offAgentEvent = client.on('agent.event', (params) => {
    const event = params as AgentEvent
    if (event?.event_id && event.run_id) callbacks.onAgentEvent(event)
  })
  const offRunStatus = client.on('run.status', (params) => {
    const value = params as { run_id?: unknown; status?: unknown }
    if (typeof value.run_id === 'string' && typeof value.status === 'string') {
      callbacks.onRunStatus(value.run_id, value.status)
    }
  })
  const offStatus = client.setStatusListener((connected) => {
    if (connected) syncPending()
  })

  // 若连接原本已建立，状态不会再次变化，需要主动同步；否则等待 connected 回调。
  const alreadyConnected = client.connected
  client.connect()
  if (alreadyConnected) syncPending()

  return () => {
    disposed = true
    offRequired()
    offResolved()
    offAgentEvent()
    offRunStatus()
    offStatus()
    client.disconnect()
  }
}
