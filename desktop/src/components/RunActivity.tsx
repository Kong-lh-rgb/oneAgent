import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { listArtifacts } from '../api/artifacts'
import { getRun, getRunTrace } from '../api/runs'
import type { AgentEvent, Run } from '../api/types'
import { mergeRunEvents } from '../agent/runAnalysis'
import {
  buildTurnView,
  formatDuration,
  formatTokens,
  toolActiveLabel,
  toolDoneLabel,
} from '../agent/turnPresentation'
import { useEventsStore } from '../stores/events'
import ContextInspector from './ContextInspector'
import ExecutionTrace from './ExecutionTrace'
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
  const toolArguments = new Map<string, unknown>()
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
      toolArguments.set(event.tool_call.id, event.tool_call.arguments)
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
        label: toolDoneLabel(
          event.tool_result.tool_name,
          event.tool_call?.arguments ?? toolArguments.get(callId),
          event.tool_result.success,
        ),
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

    if (event.type === 'tool_approval_completed') {
      const approved = event.approval_decision === 'approved'
      entries.push({
        id: event.event_id,
        label: approved ? '审批已通过' : '审批已拒绝',
        meta: event.tool_call?.name,
        state: approved ? 'done' : 'failed',
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

  return entries
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

type InspectorTab = 'run' | 'context' | 'trace'

const STATUS_LABEL: Record<string, string> = {
  pending: '准备中',
  running: '执行中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
  interrupted: '已中断',
}

export function RunInspectorOverview({
  run,
  events,
  artifactCount = 0,
}: {
  run: Run | null
  events: AgentEvent[]
  artifactCount?: number
}): React.JSX.Element {
  const view = buildTurnView(events, { now: Date.now() })
  const status = run?.status ?? view.status
  return (
    <div className="run-inspector-overview">
      <section className="inspector-section">
        <h3>概览</h3>
        {run?.user_message ? <p className="run-inspector-request">{run.user_message}</p> : null}
        <dl className="activity-overview">
          <div><dt>状态</dt><dd>{STATUS_LABEL[status] ?? status}</dd></div>
          <div><dt>模式</dt><dd>{run?.mode ?? '—'}</dd></div>
          <div><dt>步骤</dt><dd>{view.steps}</dd></div>
          <div><dt>动作</dt><dd>{view.toolCount}</dd></div>
          <div><dt>耗时</dt><dd>{formatDuration(view.durationMs) || '—'}</dd></div>
          <div><dt>Token</dt><dd>{view.usage ? formatTokens(view.usage.totalTokens) : '—'}</dd></div>
          {view.capability ? <div><dt>能力</dt><dd>{view.capability}</dd></div> : null}
          {view.targetApp ? <div><dt>目标</dt><dd>{view.targetApp}</dd></div> : null}
        </dl>
      </section>
      {view.error ? (
        <section className="inspector-error">
          <strong>{view.error.title}</strong>
          <p>{view.error.message}</p>
        </section>
      ) : null}
      <section className="inspector-section">
        <div className="inspector-section__heading">
          <h3>执行过程</h3>
          <span>{view.toolCount} actions</span>
        </div>
        <ActivityItems events={events} />
      </section>
      {artifactCount > 0 ? (
        <section className="inspector-result-summary">
          <Icon name="artifacts" size={15} />
          <div><strong>{artifactCount} 个交付结果</strong><span>可在完整 Run Detail 中打开</span></div>
        </section>
      ) : null}
    </div>
  )
}

export default function RunActivity({
  runId,
  onClose,
  onStop,
  onRecover,
  onOpenFullDetail,
}: {
  runId: string | null
  onClose?: () => void
  onStop?: () => void
  onRecover?: () => void
  onOpenFullDetail?: (runId: string) => void
}): React.JSX.Element {
  const [tab, setTab] = useState<InspectorTab>('run')
  const eventsByRun = useEventsStore((state) => state.eventsByRun)
  const runStatuses = useEventsStore((state) => state.runStatuses)
  const liveEvents = runId ? (eventsByRun[runId] ?? []) : []
  const liveStatus = runId ? runStatuses[runId] : undefined
  const running = liveStatus === 'running' || liveStatus === 'pending'
  const runQuery = useQuery({
    queryKey: ['run', runId],
    queryFn: () => getRun(runId!),
    enabled: runId !== null,
    refetchInterval: (query) => {
      const current = query.state.data as Run | undefined
      return running || current?.status === 'running' || current?.status === 'pending'
        ? 2500
        : false
    },
  })
  const durableRunning = runQuery.data?.status === 'running' || runQuery.data?.status === 'pending'
  const traceQuery = useQuery({
    queryKey: ['run-trace', runId],
    queryFn: () => getRunTrace(runId!),
    enabled: runId !== null,
    refetchInterval: running || durableRunning ? 2500 : false,
  })
  const artifactsQuery = useQuery({
    queryKey: ['artifacts', 'run', runId],
    queryFn: () => listArtifacts({ runId: runId!, limit: 100 }),
    enabled: runId !== null,
    refetchInterval: running || durableRunning ? 3000 : false,
  })
  const events = mergeRunEvents(traceQuery.data?.events ?? [], liveEvents)
  const run = runQuery.data ?? null
  const status = run?.status ?? liveStatus
  const title = run?.user_message || '当前 Run'

  return (
    <aside className="activity run-inspector" aria-label="Run inspector">
      <header className="run-inspector__header">
        <div className="run-inspector__identity">
          <strong>{title}</strong>
          <span className="mono">{runId ? `Run ${runId.slice(0, 8)}` : '尚无 Run'}</span>
        </div>
        <div className="run-inspector__header-actions">
          {status ? (
            <span className="run-inspector__status">
              <StatusDot tone={STATUS_TONE[status] ?? 'offline'} />
              {STATUS_LABEL[status] ?? status}
            </span>
          ) : null}
          {onClose ? (
            <button type="button" className="icon-btn" onClick={onClose} aria-label="关闭 Run Inspector">
              <Icon name="close" />
            </button>
          ) : null}
        </div>
      </header>
      <nav className="run-inspector__tabs" aria-label="Run inspector sections" role="tablist">
        {([
          ['run', 'Run'],
          ['context', 'Context'],
          ['trace', 'Trace'],
        ] as const).map(([id, label]) => (
          <button
            key={id}
            type="button"
            className={tab === id ? 'active' : ''}
            aria-selected={tab === id}
            role="tab"
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </nav>
      <div className="run-inspector__body">
        {runQuery.isError || traceQuery.isError ? (
          <div className="inspector-error"><strong>部分持久化数据暂时无法加载</strong><p>下面仍显示当前已收到的实时事件。</p></div>
        ) : null}
        {!runId ? (
          <EmptyState title="暂无 Run" hint="开始执行任务后可在这里分析过程。" />
        ) : tab === 'run' ? (
          <RunInspectorOverview run={run} events={events} artifactCount={artifactsQuery.data?.length ?? 0} />
        ) : tab === 'context' ? (
          <ContextInspector events={events} />
        ) : (
          <ExecutionTrace events={events} />
        )}
      </div>
      {runId ? (
        <footer className="run-inspector__footer">
          <div>
            {status === 'running' && onStop ? <button type="button" className="btn btn-danger btn-sm" onClick={onStop}>Stop</button> : null}
            {status === 'interrupted' && onRecover ? <button type="button" className="btn btn-primary btn-sm" onClick={onRecover}>Recover</button> : null}
          </div>
          {onOpenFullDetail ? <button type="button" className="btn btn-sm" onClick={() => onOpenFullDetail(runId)}>Open full detail</button> : null}
        </footer>
      ) : null}
    </aside>
  )
}
