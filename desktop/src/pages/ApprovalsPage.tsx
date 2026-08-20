import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import {
  approveApproval,
  denyApproval,
  listApprovals,
} from '../api/approvals'
import type { ApprovalRequest } from '../api/types'
import { rpcClient } from '../rpc'

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

/** Pending Approvals：显示待审批的工具调用，Approve / Deny。不做复杂 UI。 */
export default function ApprovalsPage(): React.JSX.Element {
  const queryClient = useQueryClient()
  const [notice, setNotice] = useState<string | null>(null)

  const approvalsQuery = useQuery({
    queryKey: ['approvals', 'pending'],
    queryFn: () => listApprovals('pending'),
    refetchInterval: 2000,
  })
  const approvals = approvalsQuery.data ?? []

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

  const invalidate = (): void => {
    void queryClient.invalidateQueries({ queryKey: ['approvals'] })
    void queryClient.invalidateQueries({ queryKey: ['runs'] })
  }

  const resolveMutation = useMutation({
    mutationFn: (action: { id: string; decision: 'approve' | 'deny' }) => {
      if (action.decision === 'approve') return approveApproval(action.id)
      return denyApproval(action.id)
    },
    onSuccess: () => {
      setNotice(null)
      invalidate()
    },
    onError: (err: unknown) => {
      setNotice(err instanceof Error ? err.message : String(err))
    },
  })

  return (
    <div style={{ padding: 16, overflowY: 'auto', flex: 1 }}>
      <div style={{ marginBottom: 12 }}>
        <h2 style={{ margin: 0, fontSize: 16 }}>Pending Approvals</h2>
      </div>

      {notice && <div className="error-text" style={{ marginBottom: 10 }}>{notice}</div>}

      {approvals.length === 0 ? (
        <div className="empty">暂无待审批的工具调用。</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {approvals.map((approval: ApprovalRequest) => (
            <div key={approval.id} className="approval-card">
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <strong>{approval.tool_name}</strong>
                <span className="badge badge-pending">{approval.status}</span>
              </div>
              <pre className="approval-args">{formatArguments(approval.arguments)}</pre>
              {approval.reason && (
                <div className="text-dim" style={{ marginTop: 4 }}>
                  reason: {approval.reason}
                </div>
              )}
              <div className="text-dim" style={{ marginTop: 4 }}>
                run: {approval.run_id?.slice(0, 8) ?? '-'} · created:{' '}
                {formatTime(approval.created_at)}
              </div>
              <div style={{ display: 'flex', gap: 6, marginTop: 10 }}>
                <button
                  className="btn btn-primary btn-sm"
                  disabled={resolveMutation.isPending}
                  onClick={() =>
                    resolveMutation.mutate({ id: approval.id, decision: 'approve' })
                  }
                >
                  Approve
                </button>
                <button
                  className="btn btn-danger btn-sm"
                  disabled={resolveMutation.isPending}
                  onClick={() =>
                    resolveMutation.mutate({ id: approval.id, decision: 'deny' })
                  }
                >
                  Deny
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
