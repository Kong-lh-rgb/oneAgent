import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { listRuns } from '../api/runs'
import type { RunStatus } from '../api/types'
import RunBadge from '../components/RunBadge'
import { useEventsStore } from '../stores/events'

export default function RunsPage({
  openRun,
}: {
  openRun: (runId: string) => void
}): React.JSX.Element {
  const [statusFilter, setStatusFilter] = useState<RunStatus | ''>('')
  const runStatuses = useEventsStore((state) => state.runStatuses)
  const runsQuery = useQuery({
    queryKey: ['runs', statusFilter],
    queryFn: () => listRuns({ status: statusFilter || undefined }),
    refetchInterval: 4000,
  })
  const runs = runsQuery.data ?? []

  // 用实时 run_status 修正列表里的状态展示。
  const effectiveRuns = runs.map((run) => {
    const live = runStatuses[run.id]
    return live ? { ...run, status: live as RunStatus } : run
  })

  useEffect(() => {
    document.title = 'Runs'
  }, [])

  return (
    <div style={{ padding: 16, overflowY: 'auto', flex: 1 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
        <h2 style={{ margin: 0, fontSize: 16 }}>Runs</h2>
        <select
          value={statusFilter}
          onChange={(event) => setStatusFilter(event.target.value as RunStatus | '')}
        >
          <option value="">全部状态</option>
          <option value="pending">pending</option>
          <option value="running">running</option>
          <option value="completed">completed</option>
          <option value="failed">failed</option>
          <option value="cancelled">cancelled</option>
          <option value="interrupted">interrupted</option>
        </select>
      </div>

      {runs.length === 0 ? (
        <div className="empty">暂无 Run。</div>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>run id</th>
              <th>conversation</th>
              <th>status</th>
              <th>source</th>
              <th>created_at</th>
              <th>stop_reason</th>
            </tr>
          </thead>
          <tbody>
            {effectiveRuns.map((run) => (
              <tr key={run.id}>
                <td>
                  <span className="row-link" onClick={() => openRun(run.id)}>
                    {run.id.slice(0, 8)}
                  </span>
                </td>
                <td className="text-dim">{run.conversation_id?.slice(0, 8) ?? '-'}</td>
                <td>
                  <RunBadge status={run.status} />
                </td>
                <td className="text-dim">
                  {run.source}
                  {run.source === 'automation' && run.source_id
                    ? ` · ${run.source_id.slice(0, 8)}`
                    : ''}
                </td>
                <td className="text-dim">{formatTime(run.created_at)}</td>
                <td className="text-dim">{run.stop_reason ?? '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

function formatTime(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleString()
}
