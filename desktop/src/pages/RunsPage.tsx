/** Execution History：Run 是一次执行，不重复 Conversation 历史。 */

import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'

import { listRuns } from '../api/runs'
import type { RunStatus } from '../api/types'
import { humanizeRunError } from '../agent/turnPresentation'
import { Icon } from '../components/Icon'
import { EmptyState, ErrorState, LoadingState } from '../components/PageStates'
import { PageShell } from '../components/PageShell'
import RunBadge from '../components/RunBadge'
import { useEventsStore } from '../stores/events'

type RunFilter = 'all' | 'running' | 'attention'

export default function RunsPage({
  openRun,
}: {
  openRun: (runId: string) => void
}): React.JSX.Element {
  const [filter, setFilter] = useState<RunFilter>('all')
  const runStatuses = useEventsStore((state) => state.runStatuses)
  const query = useQuery({
    queryKey: ['runs'],
    queryFn: () => listRuns({ limit: 100 }),
    refetchInterval: 4000,
  })
  const runs = useMemo(() => {
    const effective = (query.data ?? []).map((run) => ({
      ...run,
      status: (runStatuses[run.id] ?? run.status) as RunStatus,
    }))
    return effective
      .filter((run) => filter === 'all'
        || (filter === 'running' && ['running', 'pending'].includes(run.status))
        || (filter === 'attention' && ['failed', 'interrupted'].includes(run.status)))
      .sort((a, b) => {
        const activeA = ['running', 'pending', 'interrupted'].includes(a.status) ? 1 : 0
        const activeB = ['running', 'pending', 'interrupted'].includes(b.status) ? 1 : 0
        return activeB - activeA || b.created_at.localeCompare(a.created_at)
      })
  }, [filter, query.data, runStatuses])

  return (
    <PageShell
      title="Execution History"
      subtitle="Inspect a specific execution, its evidence, usage, and recovery state."
      maxWidth={980}
      actions={
        <div className="segmented-control" aria-label="Run filter">
          {(['all', 'running', 'attention'] as const).map((item) => (
            <button
              key={item}
              className={filter === item ? 'active' : ''}
              onClick={() => setFilter(item)}
            >
              {item === 'all' ? 'All' : item === 'running' ? 'Running' : 'Needs attention'}
            </button>
          ))}
        </div>
      }
    >
      {query.isPending ? <LoadingState label="Loading execution history…" />
        : query.isError ? <ErrorState message={String(query.error)} onRetry={() => void query.refetch()} />
          : runs.length === 0 ? (
            <EmptyState title="No executions in this view" hint="Runs appear when Vesta starts working." icon="runs" />
          ) : (
            <div className="run-history">
              {runs.map((run) => {
                const failed = ['failed', 'interrupted'].includes(run.status)
                const reason = failed ? humanizeRunError(run.stop_reason, run.error) : null
                return (
                  <article key={run.id} className={`run-row run-row--${run.status}`}>
                    <div className="run-row__status"><RunBadge status={run.status} /></div>
                    <button className="run-row__body" onClick={() => openRun(run.id)}>
                      <strong>{run.user_message || 'Untitled execution'}</strong>
                      <span>
                        {run.mode === 'plan' ? 'Plan' : 'Normal'} · {run.source === 'automation' ? 'Scheduled work' : 'Conversation'}
                        {' · '}{relativeTime(run.created_at)}
                      </span>
                      {reason ? <small>{reason.message}</small> : null}
                    </button>
                    <button className="run-row__open" onClick={() => openRun(run.id)} aria-label="Inspect run">
                      {run.status === 'interrupted' ? 'Recover' : <Icon name="chevronDown" size={15} />}
                    </button>
                  </article>
                )
              })}
            </div>
          )}
    </PageShell>
  )
}

function relativeTime(iso: string): string {
  const time = new Date(iso).getTime()
  if (Number.isNaN(time)) return iso
  const minutes = Math.max(0, Math.floor((Date.now() - time) / 60_000))
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes}m ago`
  if (minutes < 1440) return `${Math.floor(minutes / 60)}h ago`
  return `${Math.floor(minutes / 1440)}d ago`
}
