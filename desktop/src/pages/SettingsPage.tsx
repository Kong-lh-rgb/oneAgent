import { useQuery, useQueryClient } from '@tanstack/react-query'

import { getComputerStatus, requestComputerPermission } from '../api/computer'
import { getSystemInfo } from '../api/system'
import ComputerStatusView from '../components/ComputerStatusView'
import { ErrorState } from '../components/PageStates'
import { PageShell } from '../components/PageShell'

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
    <PageShell
      title="Settings"
      subtitle="Host connection, Computer permissions, and Desktop environment."
      maxWidth={720}
    >
      <section className="settings-section">
        <h2>Vesta Host</h2>
        {infoQuery.isLoading ? (
          <div className="text-dim"><span className="spinner" /> Checking Host…</div>
        ) : infoQuery.isError ? (
          <ErrorState
            message="Could not connect to Vesta Host"
            hint="Start python -m app.server from backend, then retry."
            onRetry={() => void infoQuery.refetch()}
          />
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
      </section>

      <section className="settings-section">
        <h2>Computer</h2>
        <ComputerStatusView
          status={computerQuery.data ?? null}
          loading={computerQuery.isLoading}
          onRequestPermission={(p) => void doRequestPermission(p)}
        />
        {(computerQuery.data?.permissions.accessibility === 'required' ||
          computerQuery.data?.permissions.screen_recording === 'required') && (
          <div className="settings-section__hint">
            Request opens the macOS permission prompt. If the change is not immediate,
            enable it in System Settings → Privacy &amp; Security.
          </div>
        )}
      </section>

      <section className="settings-section">
        <h2>Desktop</h2>
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
      </section>
    </PageShell>
  )
}
