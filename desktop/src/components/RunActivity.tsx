import type { AgentEvent } from '../api/types'
import {
  buildTurnView,
  formatDuration,
  formatTokens,
  toolActiveLabel,
  toolDoneLabel,
} from '../agent/turnPresentation'
import { useEventsStore } from '../stores/events'
import { Icon } from './Icon'
import { EmptyState, StatusDot, type StatusTone } from './ui'

export type ActivityState = 'active' | 'done' | 'failed' | 'waiting' | 'neutral'

export interface ActivityEntry {
  id: string
  label: string
  meta?: string
  state: ActivityState
  time?: string
}

/** 保留单事件到人类可读描述的转换，详细测试与 fallback 会复用。 */
export function describeActivity(event: AgentEvent): string {
  switch (event.type) {
    case 'agent_started':
      return '开始执行'
    case 'model_started':
      return '思考中'
    case 'model_completed':
      return event.message?.tool_calls?.length ? '选择下一步动作' : '生成回复'
    case 'tool_started':
      return `运行 ${event.tool_call?.name ?? '工具'}`
    case 'tool_completed':
      return event.tool_result?.success
        ? `完成 ${event.tool_result.tool_name}`
        : `失败 ${event.tool_result?.tool_name ?? '工具'}`
    case 'tool_approval_required':
      return '等待审批'
    case 'tool_approval_completed':
      return '已批准'
    case 'memory_reflection_started':
      return '保存有用上下文'
    case 'memory_reflection_completed':
      return '上下文已保存'
    case 'agent_completed':
      return '执行完成'
    case 'agent_failed':
      return '执行失败'
    default:
      return event.type.replaceAll('_', ' ')
  }
}

/**
 * 把 tool_started + tool_completed 合并成一条 Activity，隐藏模型协议噪声。
 * 原始事件仍保留在 details 中，不改变 Store 或 Trace。
 */
export function buildActivityEntries(events: AgentEvent[]): ActivityEntry[] {
  const entries: ActivityEntry[] = []
  const toolIndexes = new Map<string, number>()
  let lastThinkingIndex: number | null = null

  for (const event of events) {
    if (event.type === 'tool_started' && event.tool_call) {
      const entry: ActivityEntry = {
        id: event.tool_call.id,
        label: toolActiveLabel(event.tool_call.name, event.tool_call.arguments),
        meta: event.tool_call.name,
        state: 'active',
        time: formatEventTime(event.event_time),
      }
      toolIndexes.set(event.tool_call.id, entries.length)
      entries.push(entry)
      lastThinkingIndex = null
      continue
    }

    if (event.type === 'tool_completed' && event.tool_result) {
      const callId = event.tool_result.tool_call_id
      const existingIndex = toolIndexes.get(callId)
      const state: ActivityState = event.tool_result.success ? 'done' : 'failed'
      const next: ActivityEntry = {
        id: callId,
        label: toolDoneLabel(event.tool_result.tool_name, undefined, event.tool_result.success),
        meta: event.tool_result.tool_name,
        state,
        time: formatEventTime(event.event_time),
      }
      if (existingIndex === undefined) entries.push(next)
      else entries[existingIndex] = next
      lastThinkingIndex = null
      continue
    }

    if (event.type === 'tool_approval_required') {
      entries.push({
        id: event.event_id,
        label: '等待你的审批',
        meta: event.tool_call?.name,
        state: 'waiting',
        time: formatEventTime(event.event_time),
      })
      lastThinkingIndex = null
      continue
    }

    if (event.type === 'model_started') {
      const entry: ActivityEntry = {
        id: event.event_id,
        label: '思考下一步',
        state: 'active',
        time: formatEventTime(event.event_time),
      }
      lastThinkingIndex = entries.length
      entries.push(entry)
      continue
    }

    if (event.type === 'model_completed' && lastThinkingIndex !== null) {
      const hasTools = (event.message?.tool_calls?.length ?? 0) > 0
      entries[lastThinkingIndex] = {
        ...entries[lastThinkingIndex],
        label: hasTools ? '已选择下一步动作' : '已生成回复',
        state: 'done',
      }
      continue
    }

    if (event.type === 'memory_reflection_started') {
      entries.push({
        id: event.event_id,
        label: '保存有用上下文',
        state: 'active',
        time: formatEventTime(event.event_time),
      })
      continue
    }

    if (event.type === 'agent_completed' || event.type === 'agent_failed') {
      entries.push({
        id: event.event_id,
        label: event.type === 'agent_completed' ? '执行完成' : '执行失败',
        state: event.type === 'agent_completed' ? 'done' : 'failed',
        time: formatEventTime(event.event_time),
      })
    }
  }

  return entries.slice(-12)
}

function formatEventTime(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime())
    ? ''
    : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function rawDetail(event: AgentEvent): string {
  const parts = [`#${event.sequence}`, event.type]
  if (event.step != null) parts.push(`step ${event.step}`)
  if (event.tool_call?.name) parts.push(event.tool_call.name)
  return parts.join(' · ')
}

export function ActivityTechnicalDetails({
  events,
}: {
  events: AgentEvent[]
}): React.JSX.Element | null {
  if (events.length === 0) return null
  const providerEvent = events.find((event) => event.provider || event.model)
  return (
    <details className="activity-details activity-section">
      <summary>技术详情</summary>
      <dl className="activity-technical-meta">
        <div><dt>提供方</dt><dd>{providerEvent?.provider ?? '—'}</dd></div>
        <div><dt>模型</dt><dd>{providerEvent?.model ?? '—'}</dd></div>
      </dl>
      <div className="activity-details__list">
        {events.map((event) => (
          <details key={event.event_id}>
            <summary>{rawDetail(event)}</summary>
            <pre>{JSON.stringify(event, null, 2)}</pre>
          </details>
        ))}
      </div>
    </details>
  )
}

export function ActivityItems({ events }: { events: AgentEvent[] }): React.JSX.Element {
  const entries = buildActivityEntries(events)
  if (entries.length === 0) {
    return <EmptyState title="暂无活动" hint="运行进度会显示在这里。" />
  }
  return (
    <ol className="activity-list">
      {entries.map((entry) => (
        <li key={entry.id} className={`activity-item activity-item--${entry.state}`}>
          <span className="activity-item__marker">
            {entry.state === 'done' ? <Icon name="check" size={11} /> : null}
          </span>
          <div className="activity-item__content">
            <div>{entry.label}</div>
            {entry.time ? <time>{entry.time}</time> : null}
          </div>
        </li>
      ))}
    </ol>
  )
}

const STATUS_TONE: Record<string, StatusTone> = {
  running: 'running',
  completed: 'completed',
  failed: 'failed',
  pending: 'waiting',
  cancelled: 'offline',
  interrupted: 'waiting',
}

export default function RunActivity({
  runId,
  onClose,
}: {
  runId: string | null
  onClose?: () => void
}): React.JSX.Element {
  const eventsByRun = useEventsStore((state) => state.eventsByRun)
  const runStatuses = useEventsStore((state) => state.runStatuses)
  const events = runId ? (eventsByRun[runId] ?? []) : []
  const status = runId ? runStatuses[runId] : undefined
  const view = buildTurnView(events, { now: Date.now() })

  return (
    <aside className="activity" aria-label="Run activity">
      <div className="activity__header">
        <div>
          <strong>活动</strong>
          <span>Vesta 正在做什么</span>
        </div>
        <div className="activity__header-actions">
          {status ? <StatusDot tone={STATUS_TONE[status] ?? 'offline'} /> : null}
          {onClose ? (
            <button type="button" className="icon-btn" onClick={onClose} aria-label="关闭 Activity">
              <Icon name="close" />
            </button>
          ) : null}
        </div>
      </div>
      <div className="activity__body">
        {runId ? (
          <section className="activity-section">
            <h3>概览</h3>
            <dl className="activity-overview">
              <div><dt>状态</dt><dd>{status ?? view.status}</dd></div>
              <div><dt>运行</dt><dd className="mono">{runId.slice(0, 8)}</dd></div>
              <div><dt>步骤</dt><dd>{view.steps}</dd></div>
              <div><dt>动作</dt><dd>{view.toolCount}</dd></div>
              <div><dt>耗时</dt><dd>{formatDuration(view.durationMs) || '—'}</dd></div>
              <div><dt>Token</dt><dd>{view.usage ? formatTokens(view.usage.totalTokens) : '—'}</dd></div>
              {view.capability ? <div><dt>能力</dt><dd>{view.capability}</dd></div> : null}
              {view.targetApp ? <div><dt>目标</dt><dd>{view.targetApp}</dd></div> : null}
            </dl>
          </section>
        ) : null}
        <section className="activity-section">
          <h3>执行</h3>
          <ActivityItems events={events} />
        </section>
        <ActivityTechnicalDetails events={events} />
      </div>
    </aside>
  )
}
