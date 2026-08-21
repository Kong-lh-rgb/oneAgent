/** 电脑审批共享逻辑：过滤、队列 hook、浮动卡片（供独立浮窗使用）。

- ``isComputerApproval``：只处理 ``computer_*`` 审批。
- ``useComputerApprovalQueue``：订阅 approval.required / approval.resolved，
  维护电脑审批 FIFO 队列，并提供 approve/deny。
- ``ApprovalFloatingCard``：浮窗内的纯展示卡片（无遮罩背景）。
*/

import { useCallback, useEffect, useState } from 'react'

import { approveApproval, denyApproval, listApprovals } from '../api/approvals'
import type { ApprovalRequest } from '../api/types'
import { rpcClient } from '../rpc'
import ApprovalCard from './ApprovalCard'
import { Badge } from './ui'
import { Icon } from './Icon'

/** 是否电脑操作审批（浮窗只处理这一类）。 */
export function isComputerApproval(approval: ApprovalRequest): boolean {
  return approval.tool_name.startsWith('computer_')
}

export interface ApprovalQueueState {
  queue: ApprovalRequest[]
  active: ApprovalRequest | null
  busy: boolean
  error: string | null
  resolve: (decision: 'approve' | 'deny') => Promise<void>
}

/** 订阅审批事件，维护电脑审批 FIFO 队列。 */
export function useComputerApprovalQueue(): ApprovalQueueState {
  const [queue, setQueue] = useState<ApprovalRequest[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const enqueue = useCallback((approval: ApprovalRequest): void => {
    setQueue((prev) =>
      prev.some((item) => item.id === approval.id) ? prev : [...prev, approval],
    )
  }, [])
  const dequeue = useCallback((id: string): void => {
    setQueue((prev) => prev.filter((item) => item.id !== id))
  }, [])

  // 启动时同步一次已存在的 pending 电脑审批（浮窗在审批等待期才打开也能弹）。
  useEffect(() => {
    let cancelled = false
    void listApprovals('pending')
      .then((approvals) => {
        if (cancelled) return
        for (const approval of approvals) {
          if (isComputerApproval(approval)) enqueue(approval)
        }
      })
      .catch(() => {
        // 初次同步失败不阻塞实时事件。
      })
    return () => {
      cancelled = true
    }
  }, [enqueue])

  // 实时事件驱动：新增入队，解决出队。
  useEffect(() => {
    const offRequired = rpcClient.on('approval.required', (params) => {
      const approval = (params as { approval?: ApprovalRequest })?.approval
      if (!approval || !isComputerApproval(approval)) return
      enqueue(approval)
    })
    const offResolved = rpcClient.on('approval.resolved', (params) => {
      const id = (params as { approval?: ApprovalRequest })?.approval?.id
      if (!id) return
      dequeue(id)
    })
    return () => {
      offRequired()
      offResolved()
    }
  }, [enqueue, dequeue])

  const active = queue[0] ?? null

  const resolve = useCallback(
    async (decision: 'approve' | 'deny'): Promise<void> => {
      if (!active) return
      setBusy(true)
      setError(null)
      try {
        if (decision === 'approve') await approveApproval(active.id)
        else await denyApproval(active.id)
        // 即使 approval.resolved 通知延迟，也立即收起当前审批。
        dequeue(active.id)
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
      } finally {
        setBusy(false)
      }
    },
    [active, dequeue],
  )

  return { queue, active, busy, error, resolve }
}

export interface ApprovalFloatingCardProps {
  approval: ApprovalRequest
  queuedCount?: number
  busy?: boolean
  error?: string | null
  onApprove: (id: string) => void
  onDeny: (id: string) => void
}

/** 浮窗内的纯展示卡片（无遮罩背景）。 */
export function ApprovalFloatingCard({
  approval,
  queuedCount = 0,
  busy = false,
  error = null,
  onApprove,
  onDeny,
}: ApprovalFloatingCardProps): React.JSX.Element {
  return (
    <div className="floating-approval" data-testid="approval-floating-card">
      <div className="floating-approval__drag" />
      <div className="floating-approval__header">
        <span className="floating-approval__eyebrow">
          <Icon name="computer" size={15} />
          Computer action approval
        </span>
        {queuedCount > 1 ? (
          <Badge tone="warning">
            {queuedCount - 1} queued
          </Badge>
        ) : null}
      </div>
      <ApprovalCard
        approval={approval}
        busy={busy}
        onApprove={onApprove}
        onDeny={onDeny}
      />
      {error ? (
        <div className="error-text floating-approval__error">{error}</div>
      ) : null}
    </div>
  )
}
