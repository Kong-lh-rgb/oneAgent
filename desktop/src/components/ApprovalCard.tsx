/** 统一审批卡片视觉：Chat 与 ApprovalsPage 均可复用。 */

import type { ReactElement } from 'react'
import type { ApprovalRequest } from '../api/types'
import { Icon } from './Icon'
import { Button } from './ui'

export interface ApprovalCardProps {
  approval: ApprovalRequest
  busy?: boolean
  onApprove: (id: string) => void
  onDeny: (id: string) => void
}

export default function ApprovalCard({
  approval,
  busy = false,
  onApprove,
  onDeny,
}: ApprovalCardProps): ReactElement {
  const argsText = (() => {
    try {
      return JSON.stringify(approval.arguments, null, 2)
    } catch {
      return String(approval.arguments)
    }
  })()

  return (
    <div className="approval-card" data-testid="approval-card">
      <div className="approval-card__heading">
        <span className="approval-card__icon"><Icon name="approvals" size={17} /></span>
        <div>
          <div className="approval-card__eyebrow">Approval required</div>
          <div className="approval-card__title">Allow {approval.tool_name}?</div>
        </div>
      </div>
      {approval.reason ? (
        <div className="approval-card__reason">{approval.reason}</div>
      ) : null}
      {argsText ? (
        <details className="approval-card__details">
          <summary>Show action details</summary>
          <pre>{argsText}</pre>
        </details>
      ) : null}
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
    </div>
  )
}
