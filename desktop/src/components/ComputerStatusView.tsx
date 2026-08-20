/** Computer Host 状态展示（可复用：ComputerPage / SettingsPage）。只读展示 + 权限按钮。 */

import { leaseLabel, permissionLabel } from '../api/computer'
import type {
  ComputerPermissionName,
  ComputerPermissionStatus,
  ComputerStatus,
} from '../api/computer'

function PermissionCell({
  status,
  onRequest,
}: {
  status: ComputerPermissionStatus
  onRequest?: () => void
}): React.JSX.Element {
  const granted = status === 'granted'
  const label = permissionLabel(status)
  return (
    <div>
      <span className={granted ? '' : status === 'required' ? 'error-text' : 'text-dim'}>
        {label}
      </span>
      {!granted && onRequest ? (
        <button className="btn btn-sm" style={{ marginLeft: 8 }} onClick={() => onRequest()}>
          Request
        </button>
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
  if (loading && !status) {
    return (
      <div className="text-dim">
        <span className="spinner" /> 正在检查 Computer…
      </div>
    )
  }
  if (!status) {
    return <div className="error-text">Computer 状态不可用</div>
  }

  const reason =
    status.reason === 'helper_not_found'
      ? 'helper not found'
      : status.reason === 'unsupported_platform'
        ? `unsupported platform (${status.platform})`
        : status.reason ?? 'n/a'

  return (
    <table className="table">
      <tbody>
        <tr>
          <td className="text-muted">Computer Runtime</td>
          <td>
            <span className={status.available ? '' : 'error-text'}>
              {status.available ? `Available (${status.runtime ?? 'macos'})` : 'Unavailable'}
            </span>
            {!status.available ? <span className="text-dim"> · {reason}</span> : null}
          </td>
        </tr>
        <tr>
          <td className="text-muted">Accessibility</td>
          <td>
            <PermissionCell
              status={status.permissions.accessibility}
              onRequest={
                onRequestPermission
                  ? () => onRequestPermission('accessibility')
                  : undefined
              }
            />
          </td>
        </tr>
        <tr>
          <td className="text-muted">Screen Recording</td>
          <td>
            <PermissionCell
              status={status.permissions.screen_recording}
              onRequest={
                onRequestPermission
                  ? () => onRequestPermission('screen_recording')
                  : undefined
              }
            />
          </td>
        </tr>
        <tr>
          <td className="text-muted">Machine Lease</td>
          <td className="text-dim">{leaseLabel(status.lease)}</td>
        </tr>
        {status.helper_path ? (
          <tr>
            <td className="text-muted">helper</td>
            <td className="text-dim" style={{ wordBreak: 'break-all' }}>
              {status.helper_path}
            </td>
          </tr>
        ) : null}
      </tbody>
    </table>
  )
}
