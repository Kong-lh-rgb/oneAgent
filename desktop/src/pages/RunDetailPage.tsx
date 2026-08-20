import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { SERVER_URL } from '../api/config'
import { getLatestComputerObservation } from '../api/computer'
import { cancelRun, getRun, getRunTrace, recoverRun } from '../api/runs'
import ComputerObservationPanel from '../components/ComputerObservationPanel'
import RunBadge from '../components/RunBadge'
import TraceTimeline from '../components/TraceTimeline'

function field(label: string, value: string | null | undefined): React.JSX.Element {
  return (
    <div>
      <span className="text-muted">{label}：</span>
      <span>{value ?? '-'}</span>
    </div>
  )
}

export default function RunDetailPage({
  runId,
  onBack,
}: {
  runId: string
  onBack: () => void
}): React.JSX.Element {
  const queryClient = useQueryClient()
  const [notice, setNotice] = useState<string | null>(null)

  const runQuery = useQuery({
    queryKey: ['run', runId],
    queryFn: () => getRun(runId),
    refetchInterval: 3000,
  })
  const traceQuery = useQuery({
    queryKey: ['run-trace', runId],
    queryFn: () => getRunTrace(runId),
    refetchInterval: 3000,
  })
  const computerObservationQuery = useQuery({
    queryKey: ['computer-observation', runId],
    queryFn: () => getLatestComputerObservation(runId),
    refetchInterval: 3000,
    retry: false,
  })

  const run = runQuery.data
  const events = traceQuery.data?.events ?? []
  const computerObservation = computerObservationQuery.data

  const doCancel = async (): Promise<void> => {
    setNotice(null)
    try {
      const updated = await cancelRun(runId)
      setNotice(`已取消：${updated.status}`)
      void queryClient.invalidateQueries({ queryKey: ['run', runId] })
      void queryClient.invalidateQueries({ queryKey: ['runs'] })
    } catch (err) {
      setNotice(err instanceof Error ? err.message : String(err))
    }
  }

  const doRecover = async (): Promise<void> => {
    setNotice(null)
    try {
      const result = await recoverRun(runId)
      setNotice(`已恢复 → 新 Run ${result.run.id.slice(0, 8)}（${result.run.status}）`)
      void queryClient.invalidateQueries({ queryKey: ['run', runId] })
      void queryClient.invalidateQueries({ queryKey: ['runs'] })
    } catch (err) {
      setNotice(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <div style={{ padding: 16, overflowY: 'auto', flex: 1 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
        <button className="btn btn-sm" onClick={onBack}>← 返回</button>
        <h2 style={{ margin: 0, fontSize: 16 }}>Run Detail</h2>
        {run && <RunBadge status={run.status} />}
        {run && (
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
            <button className="btn btn-sm" onClick={() => void doCancel()} disabled={run.status !== 'running'}>
              取消
            </button>
            <button className="btn btn-sm" onClick={() => void doRecover()} disabled={run.status !== 'interrupted'}>
              恢复
            </button>
          </div>
        )}
      </div>

      {notice && <div className="text-dim" style={{ marginBottom: 10 }}>{notice}</div>}

      {run && (
        <div
          className="panel"
          style={{ padding: 12, marginBottom: 14, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}
        >
          {field('run id', run.id)}
          {field('conversation_id', run.conversation_id)}
          {field('source', run.source)}
          {run.source === 'automation' ? field('Triggered by Automation', run.source_id) : null}
          {field('source_id', run.source_id)}
          {field('mode', run.mode)}
          {field('scheduled_for', run.scheduled_for)}
          {field('triggered_at', run.triggered_at)}
          {field('recovered_from_run_id', run.recovered_from_run_id)}
          {field('created_at', run.created_at)}
          {field('started_at', run.started_at)}
          {field('completed_at', run.completed_at)}
          {field('stop_reason', run.stop_reason)}
          {field('error', run.error)}
          <div style={{ gridColumn: '1 / -1' }}>
            <span className="text-muted">user_message：</span>
            <span>{run.user_message || '-'}</span>
          </div>
        </div>
      )}

      <h3 style={{ fontSize: 14, margin: '0 0 8px' }}>Trace Timeline</h3>
      <div className="panel" style={{ padding: 12 }}>
        <TraceTimeline events={events} />
      </div>

      {computerObservation?.observation ? (
        <div className="panel" style={{ padding: 12, marginTop: 14 }}>
          <ComputerObservationPanel
            observation={computerObservation.observation}
            runId={computerObservation.run_id}
            eventTime={computerObservation.event_time}
            serverUrl={SERVER_URL}
            title="Computer Observation"
          />
        </div>
      ) : null}
    </div>
  )
}
