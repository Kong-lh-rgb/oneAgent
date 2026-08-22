/** Persistent AgentTurn：同一组件承载实时执行与完成后的 Work Record。 */

import type { AgentEvent } from '../api/types'
import {
  buildTurnView,
  formatCacheHitRate,
  formatDuration,
  formatTokens,
  type ToolStepVM,
} from '../agent/turnPresentation'
import { useEventsStore } from '../stores/events'
import { AssistantContent } from './AssistantContent'
import { Icon } from './Icon'

const STATUS = {
  thinking: { label: '正在分析', tone: 'working' },
  working: { label: '正在执行', tone: 'working' },
  waiting_approval: { label: '等待确认', tone: 'waiting' },
  verifying: { label: '正在验证', tone: 'waiting' },
  completed: { label: '已完成', tone: 'completed' },
  failed: { label: '已停止', tone: 'failed' },
  interrupted: { label: '已中断', tone: 'failed' },
  cancelled: { label: '已取消', tone: 'cancelled' },
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
              ? '等待电脑操作确认'
              : '等待你的确认'}
          </span>
        ) : tool.verification === 'unverified' ? (
          <span className="agent-action__state agent-action__state--warning">
            操作已发送 · 等待验证
          </span>
        ) : tool.verification === 'verified' ? (
          <span className="agent-action__state">已验证</span>
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
  onRecover,
  onInspect,
}: {
  runId: string | null
  step: number | null
  events: AgentEvent[]
  settling?: boolean
  streamText?: string
  onRecover?: () => void
  onInspect?: () => void
}): React.JSX.Element {
  const liveText = useEventsStore((state) =>
    runId && step !== null && step !== undefined
      ? (state.streamTextByRun[runId]?.[step] ?? '')
      : '',
  )
  const view = buildTurnView(events, { now: Date.now() })
  const status = STATUS[view.status]
  const terminal = ['completed', 'failed', 'interrupted', 'cancelled'].includes(
    view.status,
  )
  const text = streamText !== undefined ? streamText : liveText || view.finalText
  const timeline = view.tools.length > 0 ? (
    <ol className="agent-turn__timeline turn-timeline" aria-label="执行过程">
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
                恢复执行
              </button>
            ) : null}
            {onInspect ? (
              <button className="btn btn-sm" onClick={onInspect}>查看详情</button>
            ) : null}
          </div>
        </div>
      ) : text ? (
        <div className="live-turn__response agent-turn__response">
          <AssistantContent content={text} streaming={!terminal} />
          {!terminal ? <span className="stream-cursor" aria-hidden="true" /> : null}
        </div>
      ) : view.tools.length === 0 ? (
        <div className="live-turn__waiting">
          <span className="live-turn__waiting-spinner" aria-hidden="true" />
          正在执行…
        </div>
      ) : null}

      {view.steps > 0 || view.toolCount > 0 || view.usage || view.durationMs !== null ? (
        <footer className="turn-usage agent-turn__footer">
          第 {view.steps} 步
          {' · '}{view.toolCount} 次操作
          {view.usage ? (
            <>
              {' · '}输入 {formatTokens(view.usage.inputTokens)}
              {' · '}输出 {formatTokens(view.usage.outputTokens)}
              {' · '}缓存 {formatCacheHitRate(view.usage.cacheHitRate)}
            </>
          ) : null}
          {view.durationMs !== null ? ` · ${formatDuration(view.durationMs)}` : ''}
          {view.targetApp ? ` · 目标 ${view.targetApp}` : ''}
        </footer>
      ) : null}
    </section>
  )
}
