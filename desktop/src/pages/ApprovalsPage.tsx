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

const STATUS_LABEL: Record<ApprovalStatus, string> = {
  pending: '待处理',
  approved: '已批准',
  denied: '已拒绝',
}

const COMPUTER_ACTION_LABEL: Record<string, string> = {
  computer_click: '点击界面元素',
  computer_type: '输入文字',
  computer_key: '按下按键或快捷键',
  computer_scroll: '滚动当前窗口',
  computer_open_app: '打开应用',
  computer_focus_window: '聚焦窗口',
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
  const title = desktop
    ? (COMPUTER_ACTION_LABEL[approval.tool_name] ?? '操作这台 Mac')
    : approval.tool_name

  return (
    <div className="approval-card">
      <div className="approval-card__heading">
        <div className="approval-card__icon">
          <span className="approval-card__icon-label">
            {desktop ? '⌘' : '›'}
          </span>
        </div>
        <div className="approval-card__content">
          <div className="approval-card__title-row">
            <strong className="approval-card__title">{title}</strong>
            <Badge tone={STATUS_TONE[approval.status]}>{STATUS_LABEL[approval.status]}</Badge>
          </div>
          <details className="approval-card__details">
            <summary>查看详情</summary>
            <div className="approval-card__detail-body">
              {approval.reason ? (
                <div className="approval-card__reason">{approval.reason}</div>
              ) : null}
              <div className="approval-card__meta">
                {desktop ? (
                  <span className="text-muted">工具：{approval.tool_name}</span>
                ) : null}
                {approval.run_id ? (
                  <span className="text-muted">
                    Run：{approval.run_id.slice(0, 8)}
                  </span>
                ) : null}
                <span className="text-muted">{formatTime(approval.created_at)}</span>
              </div>
              <div className="approval-card__arguments">
                <span>调用参数</span>
                <pre>{formatArguments(approval.arguments)}</pre>
              </div>
            </div>
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
            批准
          </Button>
          <Button
            variant="danger"
            size="sm"
            disabled={busy}
            onClick={() => onDeny(approval.id)}
          >
            拒绝
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
      title="审批"
      subtitle="审查敏感操作；电脑操作仍会在独立浮窗中请求授权。"
      maxWidth={1360}
    >
      <section className="approvals-section">
        <h2 className="approvals-section__title">
          待处理 {pending.length > 0 ? `(${pending.length})` : ''}
        </h2>
        {pendingQuery.isPending ? (
          <LoadingState label="正在加载审批…" />
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
            title="暂无待处理审批"
            hint="沙箱操作会显示在这里；电脑操作会打开桌面浮窗。"
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
        <h2 className="approvals-section__title">最近记录</h2>
        {history.length === 0 ? (
          <div className="approval-history-empty">
            暂无最近审批记录。
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
