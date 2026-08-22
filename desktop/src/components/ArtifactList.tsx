/** 交付结果：按日期组织 Artifact，不呈现数据库表。 */

import type { Artifact } from '../api/artifacts'
import ResultCard from './ResultCard'

function dayLabel(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '更早'
  const today = new Date()
  if (date.toDateString() === today.toDateString()) return '今天'
  return date.toLocaleDateString('zh-CN', { month: 'long', day: 'numeric', year: 'numeric' })
}

export default function ArtifactList({
  artifacts,
  compact = false,
}: {
  artifacts: Artifact[]
  compact?: boolean
}): React.JSX.Element {
  if (artifacts.length === 0) return <div className="empty-inline">暂无交付结果。</div>
  if (compact) {
    return <div className="results-list">{artifacts.map((item) => <ResultCard key={item.id} artifact={item} />)}</div>
  }

  const groups = new Map<string, Artifact[]>()
  for (const artifact of artifacts) {
    const label = dayLabel(artifact.created_at)
    groups.set(label, [...(groups.get(label) ?? []), artifact])
  }
  return (
    <div className="delivered-results">
      {[...groups.entries()].map(([label, items]) => (
        <section key={label} className="result-group">
          <h2>{label}</h2>
          <div className="results-list">
            {items.map((item) => (
              <div key={item.id}>
                <ResultCard artifact={item} />
                <div className="result-origin mono">
                  {item.run_id ? `执行 ${item.run_id.slice(0, 8)}` : '执行记录不可用'}
                  {item.conversation_id ? ` · 会话 ${item.conversation_id.slice(0, 8)}` : ''}
                </div>
              </div>
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
