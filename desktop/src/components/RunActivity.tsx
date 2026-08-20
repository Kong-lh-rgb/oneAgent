import type { AgentEvent } from '../api/types'
import { useEventsStore } from '../stores/events'
import { Icon } from './Icon'
import { EmptyState, StatusDot, type StatusTone } from './ui'

export type ActivityState = 'active' | 'done' | 'failed' | 'waiting' | 'neutral'

export interface ActivityEntry {
  id: string
  label: string
  meta?: string
  state: ActivityState
}

/** 保留单事件到人类可读描述的转换，详细测试与 fallback 会复用。 */
export function describeActivity(event: AgentEvent): string {
  switch (event.type) {
    case 'agent_started':
      return 'Started working'
    case 'model_started':
      return 'Thinking'
    case 'model_completed':
      return event.message?.tool_calls?.length ? 'Choosing the next action' : 'Writing a response'
    case 'tool_started':
      return `Running ${event.tool_call?.name ?? 'tool'}`
    case 'tool_completed':
      return event.tool_result?.success
        ? `Completed ${event.tool_result.tool_name}`
        : `Failed ${event.tool_result?.tool_name ?? 'tool'}`
    case 'tool_approval_required':
      return `Waiting for approval`
    case 'tool_approval_completed':
      return 'Approval received'
    case 'memory_reflection_started':
      return 'Saving useful context'
    case 'memory_reflection_completed':
      return 'Context saved'
    case 'agent_completed':
      return 'Finished'
    case 'agent_failed':
      return 'Run failed'
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
        label: humanizeTool(event.tool_call.name, 'active'),
        meta: event.tool_call.name,
        state: 'active',
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
        label: humanizeTool(event.tool_result.tool_name, state),
        meta: event.tool_result.tool_name,
        state,
      }
      if (existingIndex === undefined) entries.push(next)
      else entries[existingIndex] = next
      lastThinkingIndex = null
      continue
    }

    if (event.type === 'tool_approval_required') {
      entries.push({
        id: event.event_id,
        label: 'Waiting for your approval',
        meta: event.tool_call?.name,
        state: 'waiting',
      })
      lastThinkingIndex = null
      continue
    }

    if (event.type === 'model_started') {
      const entry: ActivityEntry = {
        id: event.event_id,
        label: 'Thinking through the next step',
        state: 'active',
      }
      lastThinkingIndex = entries.length
      entries.push(entry)
      continue
    }

    if (event.type === 'model_completed' && lastThinkingIndex !== null) {
      const hasTools = (event.message?.tool_calls?.length ?? 0) > 0
      entries[lastThinkingIndex] = {
        ...entries[lastThinkingIndex],
        label: hasTools ? 'Selected the next action' : 'Prepared the response',
        state: 'done',
      }
      continue
    }

    if (event.type === 'memory_reflection_started') {
      entries.push({
        id: event.event_id,
        label: 'Saving useful context',
        state: 'active',
      })
      continue
    }

    if (event.type === 'agent_completed' || event.type === 'agent_failed') {
      entries.push({
        id: event.event_id,
        label: event.type === 'agent_completed' ? 'Finished the run' : 'Run failed',
        state: event.type === 'agent_completed' ? 'done' : 'failed',
      })
    }
  }

  return entries.slice(-12)
}

function humanizeTool(name: string, state: ActivityState): string {
  const readable = name.replace(/^mcp__[^_]+__/, '').replaceAll('_', ' ')
  if (state === 'active') return `Working with ${readable}`
  if (state === 'failed') return `Could not complete ${readable}`
  return `Completed ${readable}`
}

function rawDetail(event: AgentEvent): string {
  const parts = [`#${event.sequence}`, event.type]
  if (event.step != null) parts.push(`step ${event.step}`)
  if (event.tool_call?.name) parts.push(event.tool_call.name)
  return parts.join(' · ')
}

export function ActivityItems({ events }: { events: AgentEvent[] }): React.JSX.Element {
  const entries = buildActivityEntries(events)
  if (entries.length === 0) {
    return <EmptyState title="No activity yet" hint="Run progress will appear here." />
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
            {entry.meta ? <code>{entry.meta}</code> : null}
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

  return (
    <aside className="activity" aria-label="Run activity">
      <div className="activity__header">
        <div>
          <strong>Activity</strong>
          <span>What oneAgent is doing</span>
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
        <ActivityItems events={events} />
        {events.length > 0 ? (
          <details className="activity-details">
            <summary>Show technical details</summary>
            <div className="activity-details__list">
              {events.map((event) => <div key={event.event_id}>{rawDetail(event)}</div>)}
            </div>
          </details>
        ) : null}
      </div>
    </aside>
  )
}
