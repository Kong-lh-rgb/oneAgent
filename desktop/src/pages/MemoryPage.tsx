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
      <div className="memory-card__identity">
        <span className="memory-card__mark" aria-hidden="true" />
        <div>
          <header className="memory-card__header">
            <span className="mono">{memory.id}</span>
            <span>{memory.status === 'active' ? '可检索' : '已归档'}</span>
          </header>
          <h3>{memory.title}</h3>
          <p>{memory.summary}</p>
        </div>
      </div>
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
              <section className="memory-overview" aria-label="长期记忆概览">
                <div className="memory-overview__intro">
                  <span className="memory-overview__eyebrow">Memory system</span>
                  <strong>少量常驻，按需回忆</strong>
                  <p>核心记忆随每次运行进入上下文；普通记忆只提供索引，由模型在需要时读取。</p>
                </div>
                <dl className="memory-overview__stats">
                  <div><dt>使用中</dt><dd>{data.active_count}</dd></div>
                  <div><dt>容量</dt><dd>{data.max_active || '—'}</dd></div>
                  <div><dt>已归档</dt><dd>{data.archived.length}</dd></div>
                </dl>
              </section>

              <section className="memory-core">
                <div className="section-heading">
                  <div><h2>核心记忆</h2><p>稳定偏好与长期约束，每次运行都会携带</p></div>
                  <span className="memory-core__badge">常驻上下文</span>
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
                    <p>{view === 'active' ? `${memories.length} 条可通过索引按需读取` : `${memories.length} 条已退出索引但仍完整保留`}</p>
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
