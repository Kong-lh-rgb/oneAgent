/** 把 durable RPC 事件投递为桌面原生通知；本模块不保存业务状态。 */

import { rpcClient, type RpcClient } from '../rpc'

export type DesktopNotificationKind = 'approval' | 'run' | 'artifact'

export interface DesktopNotification {
  title: string
  body: string
  kind: DesktopNotificationKind
}

interface NotificationBridge {
  notify: (notification: DesktopNotification) => void
}

const TERMINAL_RUN_STATUSES = new Set([
  'completed',
  'failed',
  'cancelled',
  'interrupted',
])

/**
 * 一条共享 RpcClient 上的轻量投递器。
 * SQLite Store 仍是真实来源；进程内 key 集只用于防重连/StrictMode 重复通知。
 */
export class DesktopNotificationController {
  private readonly recentKeys = new Set<string>()
  private readonly unsubscribers: Array<() => void> = []

  constructor(
    private readonly client: Pick<RpcClient, 'on'>,
    private readonly bridge: NotificationBridge,
    private readonly isHidden: () => boolean,
    private readonly maxRecentKeys = 500,
  ) {}

  start(): void {
    if (this.unsubscribers.length > 0) return
    this.unsubscribers.push(
      this.client.on('approval.required', (params) => this.onApproval(params)),
      this.client.on('run.status', (params) => this.onRun(params)),
      this.client.on('artifact.created', (params) => this.onArtifact(params)),
    )
  }

  stop(): void {
    while (this.unsubscribers.length > 0) this.unsubscribers.pop()?.()
  }

  private onApproval(params: unknown): void {
    const data = params as { approval?: { id?: string; tool_name?: string } }
    const id = data.approval?.id
    if (!id || !this.isHidden()) return
    // Computer approval 由独立 Floating Window 负责提醒，不再发 macOS
    // 原生 Notification，避免同一审批出现双提醒。
    if (data.approval?.tool_name?.startsWith('computer_')) return
    this.deliver(`approval:${id}`, {
      title: 'Vesta needs your approval',
      body: 'A tool is waiting for your decision.',
      kind: 'approval',
    })
  }

  private onRun(params: unknown): void {
    const data = params as { run_id?: string; status?: string }
    if (
      !data.run_id ||
      !data.status ||
      !TERMINAL_RUN_STATUSES.has(data.status) ||
      !this.isHidden()
    ) return
    this.deliver(`run:${data.run_id}:${data.status}`, {
      title: `Vesta run ${data.status}`,
      body: 'Open Vesta to review the result.',
      kind: 'run',
    })
  }

  private onArtifact(params: unknown): void {
    const data = params as { artifact?: { id?: string } }
    const id = data.artifact?.id
    if (!id || !this.isHidden()) return
    this.deliver(`artifact:${id}`, {
      title: 'Vesta created a new artifact',
      body: 'Open Vesta to view the delivered result.',
      kind: 'artifact',
    })
  }

  private deliver(key: string, notification: DesktopNotification): void {
    if (this.recentKeys.has(key)) return
    this.recentKeys.add(key)
    while (this.recentKeys.size > this.maxRecentKeys) {
      const oldest = this.recentKeys.values().next().value as string | undefined
      if (oldest === undefined) break
      this.recentKeys.delete(oldest)
    }
    this.bridge.notify(notification)
  }
}

export function createDesktopNotificationController(): DesktopNotificationController | null {
  const bridge = window.vesta
  if (!bridge) return null
  return new DesktopNotificationController(
    rpcClient,
    bridge,
    () => document.visibilityState !== 'visible',
  )
}
