/** Persistent AgentTurn：同一组件承载实时执行与完成后的 Work Record。 */

import type { AgentEvent } from '../api/types'
import {
  buildTurnView,
  formatDuration,
  formatTokens,
  type ToolStepVM,
} from '../agent/turnPresentation'
import { useEventsStore } from '../stores/events'
import { AssistantContent } from './AssistantContent'
import AssistantReasoning from './AssistantReasoning'
import { Icon } from './Icon'

const STATUS = {
  thinking: { label: 'Thinking', tone: 'working' },
  working: { label: 'Working', tone: 'working' },
  waiting_approval: { label: 'Approval required', tone: 'waiting' },
  verifying: { label: 'Verifying', tone: 'waiting' },
  completed: { label: 'Completed', tone: 'completed' },
  failed: { label: 'Stopped', tone: 'failed' },
  interrupted: { label: 'Interrupted', tone: 'failed' },
  cancelled: { label: 'Cancelled', tone: 'cancelled' },
} as const

function markerFor(tool: ToolStepVM): React.JSX.Element | string {
  if (tool.state === 'done') return <Icon name="check" size={11} />
  if (tool.state === 'failed') return '×'
  return <span className="agent-action__pulse" />
}

function ToolRow({ tool }: { tool: ToolStepVM }): React.JSX.Element {
  return (
    <li className={`agent-action agent-action--${tool.state}`}>
      <span className="agent-action__marker" aria-hidden="true">
        {markerFor(tool)}
      </span>
      <div className="agent-action__body">
        <span className="agent-action__label">{tool.label}</span>
        {tool.approval === 'pending' ? (
          <span className="agent-action__state">
            {tool.isComputer
              ? 'Waiting for desktop approval'
              : 'Waiting for approval'}
          </span>
        ) : tool.verification === 'unverified' ? (
          <span className="agent-action__state agent-action__state--warning">
            Action sent · waiting for verification
          </span>
        ) : tool.verification === 'verified' ? (
          <span className="agent-action__state">Verified</span>
        ) : null}
      </div>
    </li>
  )
}

export default function LiveAgentTurn({
  runId,
  step,
  events,
  settling = false,
  streamText,
  reasoning,
  onRecover,
  onInspect,
}: {
  runId: string | null
  step: number | null
  events: AgentEvent[]
  settling?: boolean
  streamText?: string
  reasoning?: string
  onRecover?: () => void
  onInspect?: () => void
}): React.JSX.Element {
  const liveText = useEventsStore((state) =>
    runId && step !== null && step !== undefined
      ? (state.streamTextByRun[runId]?.[step] ?? '')
      : '',
  )
  const liveReasoning = useEventsStore((state) =>
    runId && step !== null && step !== undefined
      ? (state.reasoningByRun[runId]?.[step] ?? '')
      : '',
  )
  const view = buildTurnView(events, { now: Date.now() })
  const status = STATUS[view.status]
  const terminal = ['completed', 'failed', 'interrupted', 'cancelled'].includes(
    view.status,
  )
  const text = streamText !== undefined ? streamText : liveText || view.finalText
  const completedReasoning = [...events]
    .reverse()
    .find((event) => event.type === 'model_completed')?.message?.reasoning ?? ''
  const reasoningText = reasoning !== undefined
    ? reasoning
    : liveReasoning || completedReasoning

  let thinkingDuration: number | null = null
  for (let i = events.length - 1; i >= 0; i -= 1) {
    if (events[i].type !== 'model_started') continue
    const start = Date.parse(events[i].event_time)
    const completed = events.slice(i).find((event) => event.type === 'model_completed')
    if (completed) {
      const end = Date.parse(completed.event_time)
      if (!Number.isNaN(start) && !Number.isNaN(end)) {
        thinkingDuration = Math.max(0, end - start)
      }
    }
    break
  }

  const timeline = view.tools.length > 0 ? (
    <ol className="agent-turn__timeline turn-timeline">
      {view.tools.map((tool) => <ToolRow key={tool.id} tool={tool} />)}
    </ol>
  ) : null

  return (
    <section
      className={`agent-turn live-turn agent-turn--${view.status}${settling ? ' live-turn--settling' : ''}`}
      aria-live="polite"
      aria-label="Vesta Agent Turn"
      data-status={view.status}
    >
      <header className="agent-turn__header">
        <div className="message-assistant__author">
          <span
            className={`message-assistant__avatar${!terminal ? ' message-assistant__avatar--busy' : ''}`}
            aria-hidden="true"
          />
          Vesta
          <AssistantReasoning
            text={reasoningText}
            autoExpand={!terminal}
            busy={!terminal && !text && Boolean(reasoningText)}
            durationMs={terminal || text ? thinkingDuration : null}
          />
        </div>
        {view.status !== 'completed' ? (
          <span className={`agent-turn__status agent-turn__status--${status.tone}`}>
            {!terminal ? <span className="live-turn__pulse" aria-hidden="true" /> : null}
            {status.label}
          </span>
        ) : null}
      </header>

      {/* 动作时间线：仅运行中展示；聊天结束后不再显示（含 Show work 折叠块）。 */}
      {!terminal ? timeline : null}

      {view.error ? (
        <div className="agent-turn__error">
          <strong>{view.error.title}</strong>
          <p>{view.error.message}</p>
          <div className="agent-turn__error-actions">
            {view.status === 'interrupted' && onRecover ? (
              <button className="btn btn-primary btn-sm" onClick={onRecover}>
                Recover
              </button>
            ) : null}
            {onInspect ? (
              <button className="btn btn-sm" onClick={onInspect}>Inspect</button>
            ) : null}
          </div>
        </div>
      ) : text ? (
        <div className="live-turn__response agent-turn__response">
          <AssistantContent content={text} streaming={!terminal} />
          {!terminal ? <span className="stream-cursor" aria-hidden="true" /> : null}
        </div>
      ) : !reasoningText && view.tools.length === 0 ? (
        <div className="live-turn__waiting">
          <span className="live-turn__waiting-spinner" aria-hidden="true" />
          正在执行…
        </div>
      ) : null}

      {view.steps > 0 || view.toolCount > 0 || view.usage || view.durationMs !== null ? (
        <footer className="turn-usage agent-turn__footer">
          {view.steps} step{view.steps === 1 ? '' : 's'}
          {' · '}{view.toolCount} action{view.toolCount === 1 ? '' : 's'}
          {view.usage ? (
            <>
              {' · '}{formatTokens(view.usage.inputTokens)} in
              {' · '}{formatTokens(view.usage.outputTokens)} out
            </>
          ) : null}
          {view.durationMs !== null ? ` · ${formatDuration(view.durationMs)}` : ''}
          {view.targetApp ? ` · ${view.targetApp}` : ''}
        </footer>
      ) : null}
    </section>
  )
}
