/** 长期记忆观察页：Core 常驻信息 + 普通记忆 + 归档记忆。 */

import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { listMemories } from '../api/memories'
import type { LongTermMemory } from '../api/types'
import { EmptyState, ErrorState, LoadingState } from '../components/PageStates'
import { PageShell } from '../components/PageShell'

type MemoryView = 'active' | 'archived'

function formatTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN')
}

export function MemoryCard({ memory }: { memory: LongTermMemory }): React.JSX.Element {
  return (
    <article className={`memory-card memory-card--${memory.status}`}>
      <header className="memory-card__header">
        <span className="mono">{memory.id}</span>
        <span>{memory.status === 'active' ? '使用中' : '已归档'}</span>
      </header>
      <h3>{memory.title}</h3>
      <p>{memory.summary}</p>
      <dl className="memory-card__meta">
        <div><dt>版本</dt><dd>r{memory.revision}</dd></div>
        <div><dt>读取</dt><dd>{memory.access_count} 次</dd></div>
        <div><dt>更新</dt><dd>{formatTime(memory.updated_at)}</dd></div>
      </dl>
      <details className="memory-card__details">
        <summary>查看完整内容</summary>
        <div className="memory-card__content">{memory.content}</div>
        {memory.last_update_reason ? <small>更新原因：{memory.last_update_reason}</small> : null}
        {memory.archive_reason ? <small>归档原因：{memory.archive_reason}</small> : null}
      </details>
    </article>
  )
}

export default function MemoryPage(): React.JSX.Element {
  const [view, setView] = useState<MemoryView>('active')
  const query = useQuery({
    queryKey: ['memories'],
    queryFn: listMemories,
    refetchInterval: 10_000,
  })
  const data = query.data
  const memories = view === 'active' ? (data?.active ?? []) : (data?.archived ?? [])

  return (
    <PageShell
      title="长期记忆"
      subtitle="查看 Vesta 跨会话保留的稳定信息与历史经验。"
      maxWidth={1080}
      actions={
        <div className="segmented-control" aria-label="记忆筛选">
          <button className={view === 'active' ? 'active' : ''} onClick={() => setView('active')}>
            使用中 {data ? `(${data.active_count})` : ''}
          </button>
          <button className={view === 'archived' ? 'active' : ''} onClick={() => setView('archived')}>
            已归档 {data ? `(${data.archived.length})` : ''}
          </button>
        </div>
      }
    >
      {query.isPending ? <LoadingState label="正在加载长期记忆…" />
        : query.isError ? <ErrorState message={String(query.error)} onRetry={() => void query.refetch()} />
          : data ? (
            <div className="memory-page">
              <section className="memory-core">
                <div className="section-heading">
                  <div><h2>核心记忆</h2><p>每次运行都会携带的少量长期信息</p></div>
                  <span>{data.active_count} / {data.max_active || '—'} 条普通记忆</span>
                </div>
                {data.core.trim() ? (
                  <div className="memory-core__content">{data.core}</div>
                ) : (
                  <p className="memory-empty-hint">暂无核心记忆。当用户明确表达稳定偏好或长期约束后，系统会在这里保留。</p>
                )}
              </section>

              <section className="memory-list-section">
                <div className="section-heading">
                  <div>
                    <h2>{view === 'active' ? '普通记忆' : '归档记忆'}</h2>
                    <p>{view === 'active' ? '模型可通过索引按需读取' : '不再进入索引，但仍然完整保留'}</p>
                  </div>
                </div>
                {memories.length === 0 ? (
                  <EmptyState
                    title={view === 'active' ? '暂无普通记忆' : '暂无归档记忆'}
                    hint={view === 'active' ? '有长期价值的信息会在 Run 完成后由系统整理。' : '过期或被替代的记忆会保留在这里。'}
                    icon="memory"
                  />
                ) : (
                  <div className="memory-grid">
                    {memories.map((memory) => <MemoryCard key={memory.id} memory={memory} />)}
                  </div>
                )}
              </section>
            </div>
          ) : null}
    </PageShell>
  )
}
