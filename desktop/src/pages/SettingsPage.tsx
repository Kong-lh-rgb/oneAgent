import { useQuery } from '@tanstack/react-query'

import { getHealth } from '../api/health'

export default function SettingsPage(): React.JSX.Element {
  const healthQuery = useQuery({
    queryKey: ['health'],
    queryFn: () => getHealth(),
    refetchInterval: 5000,
    retry: false,
  })

  const desktop = window.oneagent

  return (
    <div style={{ padding: 16, overflowY: 'auto', flex: 1, maxWidth: 640 }}>
      <h2 style={{ margin: 0, fontSize: 16, marginBottom: 12 }}>Settings</h2>

      <div className="panel" style={{ padding: 14 }}>
        <h3 style={{ fontSize: 14, marginTop: 0 }}>Backend</h3>
        {healthQuery.isLoading ? (
          <div className="text-dim"><span className="spinner" /> 正在检查后端…</div>
        ) : healthQuery.isError ? (
          <div className="error-text">
            无法连接 Agent Server。请先在 backend 启动：
            <pre style={{ background: 'var(--bg)', padding: 10, borderRadius: 6 }}>{'python -m app.server'}</pre>
          </div>
        ) : (
          <table className="table">
            <tbody>
              <tr>
                <td className="text-muted">health</td>
                <td className="text-dim">{healthQuery.data?.status ?? '-'}</td>
              </tr>
              <tr>
                <td className="text-muted">provider</td>
                <td>{healthQuery.data?.provider ?? '-'}</td>
              </tr>
              <tr>
                <td className="text-muted">model</td>
                <td>{healthQuery.data?.model ?? '-'}</td>
              </tr>
              <tr>
                <td className="text-muted">server version</td>
                <td className="text-dim">{healthQuery.data?.version ?? '-'}</td>
              </tr>
              <tr>
                <td className="text-muted">database</td>
                <td className="text-dim">backend/.oneagent/oneagent.db</td>
              </tr>
            </tbody>
          </table>
        )}
      </div>

      <div className="panel" style={{ padding: 14, marginTop: 12 }}>
        <h3 style={{ fontSize: 14, marginTop: 0 }}>Desktop</h3>
        <table className="table">
          <tbody>
            <tr>
              <td className="text-muted">app version</td>
              <td>0.1.0 (V0)</td>
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
