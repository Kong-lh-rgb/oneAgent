/** Approvals 页面（UX 样板）：Pending / History 分段，统一 PageShell + toast。

- desktop 审批显示人类可读动作（如 “Type text”），技术 tool_name 放次要位置。
- Approve / Deny 用全局 toast 反馈，替代 inline notice。
- 加载 / 空 / 错误用统一 PageStates。
*/

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'

import {
  approveApproval,
  denyApproval,
  listApprovals,
} from '../api/approvals'
import type { ApprovalRequest, ApprovalStatus } from '../api/types'
import {
  computerActionLabel,
  isDesktopApproval,
} from '../approval/computerApproval'
import { PageShell } from '../components/PageShell'
import { EmptyState, ErrorState, LoadingState } from '../components/PageStates'
import { Badge, Button } from '../components/ui'
import { rpcClient } from '../rpc'
import { toast } from '../stores/toasts'

function formatTime(iso: string | null): string {
  if (!iso) return '-'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleString()
}

function formatArguments(argumentsValue: Record<string, unknown>): string {
  try {
    return JSON.stringify(argumentsValue, null, 2)
  } catch {
    return String(argumentsValue)
  }
}

const STATUS_TONE: Record<ApprovalStatus, 'warning' | 'success' | 'danger'> = {
  pending: 'warning',
  approved: 'success',
  denied: 'danger',
}

function ApprovalItem({
  approval,
  busy,
  onApprove,
  onDeny,
}: {
  approval: ApprovalRequest
  busy: boolean
  onApprove: (id: string) => void
  onDeny: (id: string) => void
}): React.JSX.Element {
  const desktop = isDesktopApproval(approval)
  const title = desktop ? computerActionLabel(approval) : approval.tool_name

  return (
    <div className="approval-card">
      <div className="approval-card__heading">
        <div className="approval-card__icon">
          <span className="approval-card__icon-label">
            {desktop ? '⌘' : '›'}
          </span>
        </div>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <strong className="approval-card__title">{title}</strong>
            <Badge tone={STATUS_TONE[approval.status]}>{approval.status}</Badge>
          </div>
          {approval.reason ? (
            <div className="approval-card__reason">{approval.reason}</div>
          ) : null}
          <div className="approval-card__meta">
            {desktop ? (
              <span className="text-muted">{approval.tool_name}</span>
            ) : null}
            {approval.run_id ? (
              <span className="text-muted">
                run: {approval.run_id.slice(0, 8)}
              </span>
            ) : null}
            <span className="text-muted">{formatTime(approval.created_at)}</span>
          </div>
          <details className="approval-card__details">
            <summary>Show arguments</summary>
            <pre>{formatArguments(approval.arguments)}</pre>
          </details>
        </div>
      </div>

      {approval.status === 'pending' ? (
        <div className="approval-card__actions">
          <Button
            variant="primary"
            size="sm"
            disabled={busy}
            onClick={() => onApprove(approval.id)}
          >
            Approve
          </Button>
          <Button
            variant="danger"
            size="sm"
            disabled={busy}
            onClick={() => onDeny(approval.id)}
          >
            Deny
          </Button>
        </div>
      ) : null}
    </div>
  )
}

/** Approvals 页面：Pending 与 History 分段。 */
export default function ApprovalsPage(): React.JSX.Element {
  const queryClient = useQueryClient()

  const pendingQuery = useQuery({
    queryKey: ['approvals', 'pending'],
    queryFn: () => listApprovals('pending'),
    refetchInterval: 2000,
  })
  const historyQuery = useQuery({
    queryKey: ['approvals', 'history'],
    queryFn: () => listApprovals(undefined, 50),
    refetchInterval: 5000,
  })
  const pending = pendingQuery.data ?? []
  const history = (historyQuery.data ?? [])
    .filter((approval) => approval.status !== 'pending')
    .slice(0, 20)

  // 收到 approval.required / approval.resolved 通知时立即刷新。
  useEffect(() => {
    const refresh = (): void => {
      void queryClient.invalidateQueries({ queryKey: ['approvals'] })
      void queryClient.invalidateQueries({ queryKey: ['runs'] })
    }
    const offRequired = rpcClient.on('approval.required', refresh)
    const offResolved = rpcClient.on('approval.resolved', refresh)
    return () => {
      offRequired()
      offResolved()
    }
  }, [queryClient])

  const resolveMutation = useMutation({
    mutationFn: (action: { id: string; decision: 'approve' | 'deny' }) =>
      action.decision === 'approve'
        ? approveApproval(action.id)
        : denyApproval(action.id),
    onSuccess: (approval, action) => {
      toast.success(
        action.decision === 'approve'
          ? `已批准 ${approval.tool_name}`
          : `已拒绝 ${approval.tool_name}`,
      )
      void queryClient.invalidateQueries({ queryKey: ['approvals'] })
      void queryClient.invalidateQueries({ queryKey: ['runs'] })
    },
    onError: (err: unknown) => {
      toast.error(err instanceof Error ? err.message : String(err))
    },
  })

  return (
    <PageShell
      title="Approvals"
      subtitle="工具调用的人工审批：桌面操作走浮动小窗，对话内审批在这里。"
    >
      <section className="approvals-section">
        <h2 className="approvals-section__title">
          Pending {pending.length > 0 ? `(${pending.length})` : ''}
        </h2>
        {pendingQuery.isPending ? (
          <LoadingState label="正在加载待审批…" />
        ) : pendingQuery.isError ? (
          <ErrorState
            message={String(pendingQuery.error)}
            onRetry={() =>
              void queryClient.invalidateQueries({
                queryKey: ['approvals'],
              })
            }
          />
        ) : pending.length === 0 ? (
          <EmptyState
            title="没有待审批的工具调用"
            hint="Agent 请求人工确认时会出现在这里；电脑操作审批会直接浮到桌面。"
            icon="approvals"
          />
        ) : (
          <div className="approvals-list">
            {pending.map((approval) => (
              <ApprovalItem
                key={approval.id}
                approval={approval}
                busy={resolveMutation.isPending}
                onApprove={(id) =>
                  resolveMutation.mutate({ id, decision: 'approve' })
                }
                onDeny={(id) =>
                  resolveMutation.mutate({ id, decision: 'deny' })
                }
              />
            ))}
          </div>
        )}
      </section>

      <section className="approvals-section">
        <h2 className="approvals-section__title">Recent</h2>
        {history.length === 0 ? (
          <div className="text-muted" style={{ padding: '8px 2px' }}>
            暂无已处理的审批记录。
          </div>
        ) : (
          <div className="approvals-list">
            {history.map((approval) => (
              <ApprovalItem
                key={approval.id}
                approval={approval}
                busy={false}
                onApprove={() => {}}
                onDeny={() => {}}
              />
            ))}
          </div>
        )}
      </section>
    </PageShell>
  )
}
