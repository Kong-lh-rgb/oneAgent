/** Computer Page：状态 / 最新 screenshot / 结构化 Observation。只读视图。 */

import { useQuery, useQueryClient } from '@tanstack/react-query'

import { SERVER_URL } from '../api/config'
import {
  getComputerStatus,
  getLatestComputerObservation,
  requestComputerPermission,
} from '../api/computer'
import ComputerObservationPanel from '../components/ComputerObservationPanel'
import ComputerStatusView from '../components/ComputerStatusView'
import { PageShell } from '../components/PageShell'

export default function ComputerPage(): React.JSX.Element {
  const queryClient = useQueryClient()

  const statusQuery = useQuery({
    queryKey: ['computer-status'],
    queryFn: () => getComputerStatus(),
    refetchInterval: 2500,
    retry: false,
  })

  const observationQuery = useQuery({
    queryKey: ['computer-observation'],
    queryFn: () => getLatestComputerObservation(),
    refetchInterval: 2500,
    retry: false,
  })

  const doRequestPermission = async (permission: 'accessibility' | 'screen_recording') => {
    try {
      await requestComputerPermission(permission)
      void queryClient.invalidateQueries({ queryKey: ['computer-status'] })
    } catch (err) {
      console.warn('computer permission request failed', err)
    }
  }

  const latest = observationQuery.data

  return (
    <PageShell
      title="Computer"
      subtitle="本机电脑状态与最近一次屏幕观察（只读）。"
    >
      <div className="panel" style={{ padding: 14 }}>
        <h3 style={{ fontSize: 14, marginTop: 0 }}>Status</h3>
        <ComputerStatusView
          status={statusQuery.data ?? null}
          loading={statusQuery.isLoading}
          onRequestPermission={(p) => void doRequestPermission(p)}
        />
      </div>

      <div className="panel" style={{ padding: 14, marginTop: 12 }}>
        <ComputerObservationPanel
          observation={latest?.observation ?? null}
          runId={latest?.run_id ?? null}
          eventTime={latest?.event_time ?? null}
          serverUrl={SERVER_URL}
        />
      </div>
    </PageShell>
  )
}
