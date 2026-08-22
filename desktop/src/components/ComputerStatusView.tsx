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
        <button className="btn btn-sm" onClick={onRequest}>请求权限</button>
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
  if (loading && !status) return <div className="loading-inline"><span className="spinner" /> 正在检查电脑操作状态…</div>
  if (!status) return <div className="empty-inline empty-inline--error">无法获取电脑操作状态</div>

  const reason = status.reason === 'helper_not_found'
    ? '未找到原生 helper'
    : status.reason === 'unsupported_platform'
      ? `不支持当前平台（${status.platform}）`
      : status.reason

  return (
    <div className="computer-status-view">
      <div className="computer-status-view__runtime">
        <StatusDot tone={status.available ? 'ready' : 'failed'} />
        <div>
          <strong>{status.available ? '可用' : '不可用'}</strong>
          <span>
            {status.available
              ? `电脑操作运行时 · ${status.runtime ?? 'macOS'}`
              : reason ?? '运行时不可用'}
          </span>
        </div>
        <small>{leaseLabel(status.lease)}</small>
      </div>
      <div className="permission-list">
        <PermissionRow
          name="辅助功能"
          status={status.permissions.accessibility}
          onRequest={onRequestPermission
            ? () => onRequestPermission('accessibility')
            : undefined}
        />
        <PermissionRow
          name="屏幕录制"
          status={status.permissions.screen_recording}
          onRequest={onRequestPermission
            ? () => onRequestPermission('screen_recording')
            : undefined}
        />
      </div>
      {status.helper_path ? (
        <details className="technical-inline">
          <summary>运行时详情</summary>
          <code>{status.helper_path}</code>
        </details>
      ) : null}
    </div>
  )
}
