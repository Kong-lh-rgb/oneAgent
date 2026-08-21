import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { listRuns } from '../api/runs'
import type { RunStatus } from '../api/types'
import { EmptyState, ErrorState, LoadingState } from '../components/PageStates'
import { PageShell } from '../components/PageShell'
import RunBadge from '../components/RunBadge'
import { useEventsStore } from '../stores/events'

const STATUS_OPTIONS: Array<{ value: RunStatus | ''; label: string }> = [
  { value: '', label: '全部状态' },
  { value: 'pending', label: 'pending' },
  { value: 'running', label: 'running' },
  { value: 'completed', label: 'completed' },
  { value: 'failed', label: 'failed' },
  { value: 'cancelled', label: 'cancelled' },
  { value: 'interrupted', label: 'interrupted' },
]

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
    <PageShell
      title="Runs"
      subtitle="任务运行记录：状态、来源与结束原因。"
      actions={
        <select
          className="page-select"
          value={statusFilter}
          aria-label="状态筛选"
          onChange={(event) =>
            setStatusFilter(event.target.value as RunStatus | '')
          }
        >
          {STATUS_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      }
    >
      {runsQuery.isPending ? (
        <LoadingState label="正在加载 Runs…" />
      ) : runsQuery.isError ? (
        <ErrorState
          message={String(runsQuery.error)}
          onRetry={() => void runsQuery.refetch()}
        />
      ) : runs.length === 0 ? (
        <EmptyState
          title={statusFilter ? '该状态下暂无 Run' : '暂无 Run'}
          hint="启动一次对话或 Automation 后，运行记录会出现在这里。"
          icon="runs"
        />
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
    </PageShell>
  )
}

function formatTime(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleString()
}
