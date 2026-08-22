/** 执行历史：Run 是一次执行，不重复 Conversation 历史。 */

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
      title="执行历史"
      subtitle="查看每次执行的状态、证据、用量与恢复点。"
      maxWidth={1120}
      actions={
        <div className="segmented-control" aria-label="Run filter">
          {(['all', 'running', 'attention'] as const).map((item) => (
            <button
              key={item}
              className={filter === item ? 'active' : ''}
              onClick={() => setFilter(item)}
            >
              {item === 'all' ? '全部' : item === 'running' ? '运行中' : '需要关注'}
            </button>
          ))}
        </div>
      }
    >
      {query.isPending ? <LoadingState label="正在加载执行历史…" />
        : query.isError ? <ErrorState message={String(query.error)} onRetry={() => void query.refetch()} />
          : runs.length === 0 ? (
            <EmptyState title="当前没有执行记录" hint="Vesta 开始处理工作后，Run 会出现在这里。" icon="runs" />
          ) : (
            <div className="run-history">
              {runs.map((run) => {
                const failed = ['failed', 'interrupted'].includes(run.status)
                const reason = failed ? humanizeRunError(run.stop_reason, run.error) : null
                return (
                  <article key={run.id} className={`run-card run-card--${run.status}`}>
                    <button className="run-card__button" onClick={() => openRun(run.id)}>
                      <header className="run-card__header">
                        <RunBadge status={run.status} />
                        <span className="mono">{run.id.slice(0, 8)}</span>
                      </header>
                      <strong className="run-card__title">{run.user_message || '未命名执行'}</strong>
                      <div className="run-card__meta">
                        <span>{run.mode === 'plan' ? '计划模式' : '普通模式'}</span>
                        <span>{run.source === 'automation' ? '自动化触发' : '会话触发'}</span>
                      </div>
                      {reason ? <small className="run-card__error">{reason.message}</small> : null}
                      <footer className="run-card__footer">
                        <time>{relativeTime(run.created_at)}</time>
                        <span>{run.status === 'interrupted' ? '查看恢复' : '查看详情'} <Icon name="chevronDown" size={14} /></span>
                      </footer>
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
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  if (minutes < 1440) return `${Math.floor(minutes / 60)} 小时前`
  return `${Math.floor(minutes / 1440)} 天前`
}
