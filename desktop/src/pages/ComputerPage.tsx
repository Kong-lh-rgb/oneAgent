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
    <div style={{ padding: 16, overflowY: 'auto', flex: 1, maxWidth: 960 }}>
      <h2 style={{ margin: 0, fontSize: 16, marginBottom: 12 }}>Computer</h2>

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
    </div>
  )
}
