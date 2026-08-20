import { useQuery, useQueryClient } from '@tanstack/react-query'

import { getComputerStatus, requestComputerPermission } from '../api/computer'
import { getSystemInfo } from '../api/system'
import ComputerStatusView from '../components/ComputerStatusView'

export default function SettingsPage(): React.JSX.Element {
  const queryClient = useQueryClient()

  const infoQuery = useQuery({
    queryKey: ['system-info'],
    queryFn: () => getSystemInfo(),
    refetchInterval: 5000,
    retry: false,
  })

  const computerQuery = useQuery({
    queryKey: ['computer-status'],
    queryFn: () => getComputerStatus(),
    refetchInterval: 5000,
    retry: false,
  })

  const desktop = window.vesta

  const doRequestPermission = async (
    permission: 'accessibility' | 'screen_recording',
  ): Promise<void> => {
    try {
      await requestComputerPermission(permission)
      void queryClient.invalidateQueries({ queryKey: ['computer-status'] })
    } catch (err) {
      console.warn('computer permission request failed', err)
    }
  }

  return (
    <div style={{ padding: 16, overflowY: 'auto', flex: 1, maxWidth: 640 }}>
      <h2 style={{ margin: 0, fontSize: 16, marginBottom: 12 }}>Settings</h2>

      <div className="panel" style={{ padding: 14 }}>
        <h3 style={{ fontSize: 14, marginTop: 0 }}>Vesta Host</h3>
        {infoQuery.isLoading ? (
          <div className="text-dim"><span className="spinner" /> 正在检查后端…</div>
        ) : infoQuery.isError ? (
          <div className="error-text">
            无法连接 Vesta Host。请先在 backend 启动：
            <pre style={{ background: 'var(--bg)', padding: 10, borderRadius: 6 }}>{'python -m app.server'}</pre>
          </div>
        ) : (
          <table className="table">
            <tbody>
              <tr>
                <td className="text-muted">host status</td>
                <td className="text-dim">{infoQuery.data?.status ?? '-'}</td>
              </tr>
              <tr>
                <td className="text-muted">provider</td>
                <td>{infoQuery.data?.provider ?? '-'}</td>
              </tr>
              <tr>
                <td className="text-muted">model</td>
                <td>{infoQuery.data?.model ?? '-'}</td>
              </tr>
              <tr>
                <td className="text-muted">host version</td>
                <td className="text-dim">{infoQuery.data?.version ?? '-'}</td>
              </tr>
              <tr>
                <td className="text-muted">database</td>
                <td className="text-dim">{infoQuery.data?.database ?? '-'}</td>
              </tr>
            </tbody>
          </table>
        )}
      </div>

      <div className="panel" style={{ padding: 14, marginTop: 12 }}>
        <h3 style={{ fontSize: 14, marginTop: 0 }}>Computer</h3>
        <ComputerStatusView
          status={computerQuery.data ?? null}
          loading={computerQuery.isLoading}
          onRequestPermission={(p) => void doRequestPermission(p)}
        />
        {(computerQuery.data?.permissions.accessibility === 'required' ||
          computerQuery.data?.permissions.screen_recording === 'required') && (
          <div className="text-dim" style={{ marginTop: 8 }}>
            点击 Request 会打开系统权限提示；如果未立即生效，请到
            System Settings → Privacy &amp; Security 手动开启。
          </div>
        )}
      </div>

      <div className="panel" style={{ padding: 14, marginTop: 12 }}>
        <h3 style={{ fontSize: 14, marginTop: 0 }}>Desktop</h3>
        <table className="table">
          <tbody>
            <tr>
              <td className="text-muted">app version</td>
              <td>0.1.0</td>
            </tr>
            <tr>
              <td className="text-muted">platform</td>
              <td className="text-dim">{desktop?.platform ?? 'web'}</td>
            </tr>
            <tr>
              <td className="text-muted">electron</td>
              <td className="text-dim">{desktop?.versions.electron ?? '-'}</td>
            </tr>
            <tr>
              <td className="text-muted">chrome</td>
              <td className="text-dim">{desktop?.versions.chrome ?? '-'}</td>
            </tr>
            <tr>
              <td className="text-muted">node</td>
              <td className="text-dim">{desktop?.versions.node ?? '-'}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  )
}
