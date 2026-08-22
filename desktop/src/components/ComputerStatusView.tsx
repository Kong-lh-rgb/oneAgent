/** Computer Runtime 与权限的紧凑产品状态。 */

import { leaseLabel, permissionLabel } from '../api/computer'
import type {
  ComputerPermissionName,
  ComputerPermissionStatus,
  ComputerStatus,
} from '../api/computer'
import { StatusDot } from './ui'

function PermissionRow({
  name,
  status,
  onRequest,
}: {
  name: string
  status: ComputerPermissionStatus
  onRequest?: () => void
}): React.JSX.Element {
  return (
    <div className="permission-row">
      <span>{name}</span>
      <span className={`permission-row__state permission-row__state--${status}`}>
        {permissionLabel(status)}
      </span>
      {status !== 'granted' && onRequest ? (
        <button className="btn btn-sm" onClick={onRequest}>Request</button>
      ) : null}
    </div>
  )
}

export default function ComputerStatusView({
  status,
  loading = false,
  onRequestPermission,
}: {
  status: ComputerStatus | null
  loading?: boolean
  onRequestPermission?: (permission: ComputerPermissionName) => void
}): React.JSX.Element {
  if (loading && !status) return <div className="loading-inline"><span className="spinner" /> Checking Computer…</div>
  if (!status) return <div className="empty-inline empty-inline--error">Computer status unavailable</div>

  const reason = status.reason === 'helper_not_found'
    ? 'helper not found'
    : status.reason === 'unsupported_platform'
      ? `unsupported platform (${status.platform})`
      : status.reason

  return (
    <div className="computer-status-view">
      <div className="computer-status-view__runtime">
        <StatusDot tone={status.available ? 'ready' : 'failed'} />
        <div>
          <strong>{status.available ? 'Available' : 'Unavailable'}</strong>
          <span>
            {status.available
              ? `Computer Runtime · ${status.runtime ?? 'macOS'}`
              : reason ?? 'Runtime not available'}
          </span>
        </div>
        <small>{leaseLabel(status.lease)}</small>
      </div>
      <div className="permission-list">
        <PermissionRow
          name="Accessibility"
          status={status.permissions.accessibility}
          onRequest={onRequestPermission
            ? () => onRequestPermission('accessibility')
            : undefined}
        />
        <PermissionRow
          name="Screen Recording"
          status={status.permissions.screen_recording}
          onRequest={onRequestPermission
            ? () => onRequestPermission('screen_recording')
            : undefined}
        />
      </div>
      {status.helper_path ? (
        <details className="technical-inline">
          <summary>Runtime details</summary>
          <code>{status.helper_path}</code>
        </details>
      ) : null}
    </div>
  )
}
