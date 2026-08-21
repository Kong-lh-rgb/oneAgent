/** 当前 Run 的内联工作区：连续执行流（Thinking → Tool → Approval → Verify → Final → Usage）。

- 细粒度 selector：只订阅当前 run+step 的流式正文/思考，不让父组件每 token 重渲染。
- 事件 → ViewModel 全部走 turnPresentation（纯逻辑），组件只负责渲染。
- 主界面不 dump 原始 arguments；技术细节折叠在 “Show technical details”。
*/

import type { AgentEvent } from '../api/types'
import {
  buildTurnView,
  formatDuration,
  formatTokens,
  type ToolStepVM,
} from '../agent/turnPresentation'
import { useEventsStore } from '../stores/events'
import { Icon } from './Icon'
import { AssistantContent } from './AssistantContent'
import AssistantReasoning from './AssistantReasoning'

function markerFor(state: ToolStepVM['state']): string {
  if (state === 'done') return '✓'
  if (state === 'failed') return '✕'
  return '●'
}

function ToolRow({ tool }: { tool: ToolStepVM }): React.JSX.Element {
  return (
    <li className={`turn-tool turn-tool--${tool.state}`}>
      <span
        className={`turn-tool__marker turn-tool__marker--${tool.state}`}
        aria-hidden="true"
      >
        {markerFor(tool.state)}
      </span>
      <span className="turn-tool__label">{tool.label}</span>
      {tool.approval === 'pending' ? (
        <span className="turn-tool__hint">
          {tool.isComputer ? 'Waiting for desktop approval' : 'Waiting for approval'}
        </span>
      ) : null}
      {tool.verification === 'unverified' ? (
        <span className="turn-tool__hint turn-tool__hint--warn">
          Action sent · result not yet verified
        </span>
      ) : null}
      {tool.details ? (
        <details className="turn-tool__details">
          <summary>Show technical details</summary>
          <pre>{tool.details}</pre>
        </details>
      ) : null}
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
}: {
  runId: string | null
  step: number | null
  events: AgentEvent[]
  settling?: boolean
  /** 可选覆盖（测试/复用）；缺省由内部 selector 订阅流式文本。 */
  streamText?: string
  /** 可选覆盖（测试/复用）；缺省由内部 selector 订阅流式思考。 */
  reasoning?: string
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

  const text = streamText !== undefined ? streamText : liveText
  const streamedReasoning = reasoning !== undefined ? reasoning : liveReasoning
  const completedReasoning =
    [...events]
      .reverse()
      .find((event) => event.type === 'model_completed')?.message?.reasoning ?? ''
  const reasoningText = streamedReasoning || completedReasoning

  const view = buildTurnView(events, { now: Date.now() })
  const isStreaming = !settling

  // 最近一次思考的耗时（最近 model_started → 其后 model_completed）。
  let thinkingDuration: number | null = null
  for (let i = events.length - 1; i >= 0; i -= 1) {
    if (events[i].type === 'model_started') {
      const start = Date.parse(events[i].event_time)
      for (let j = i; j < events.length; j += 1) {
        if (events[j].type === 'model_completed') {
          const end = Date.parse(events[j].event_time)
          if (!Number.isNaN(start) && !Number.isNaN(end)) {
            thinkingDuration = Math.max(0, end - start)
          }
          break
        }
      }
      break
    }
  }

  return (
    <section
      className={`live-turn${settling ? ' live-turn--settling' : ''}`}
      aria-live="polite"
      aria-label="Vesta 实时执行过程"
    >
      {/* 与最终 assistant 消息共用的作者行，仅多一个 live 指示点：
          回复完成、容器被正式消息替换时，视觉不会跳变。 */}
      <div className="message-assistant__author">
        <span className="message-assistant__avatar"><Icon name="agent" size={13} /></span>
        Vesta
        <span className="live-turn__pulse" aria-hidden="true" />
      </div>

      {/* Thinking：思考中自动展开，正文开始后平滑收起（最新思考可展开）。 */}
      <AssistantReasoning
        text={reasoningText}
        autoExpand={!text}
        busy={!text && Boolean(reasoningText)}
        durationMs={text ? thinkingDuration : null}
      />

      {/* Tool / Approval / Verification timeline（compact, terminal-like）。 */}
      {view.tools.length > 0 ? (
        <ol className="turn-timeline">
          {view.tools.map((tool) => (
            <ToolRow key={tool.id} tool={tool} />
          ))}
        </ol>
      ) : null}

      {/* Final answer 实时输出。 */}
      {text ? (
        <div className="live-turn__response">
          <AssistantContent content={text} streaming={isStreaming} />
          <span className="stream-cursor" aria-hidden="true" />
        </div>
      ) : !reasoningText && view.tools.length === 0 ? (
        <div className="live-turn__waiting">
          <span className="live-turn__waiting-spinner" aria-hidden="true" />
          正在执行…
        </div>
      ) : null}

      {/* Usage footer：steps · tools · tokens · duration。 */}
      {view.usage ? (
        <div className="turn-usage">
          {view.steps} step{view.steps === 1 ? '' : 's'} · {view.toolCount} tool
          {view.toolCount === 1 ? '' : 's'} ·{' '}
          {formatTokens(view.usage.inputTokens)} in ·{' '}
          {formatTokens(view.usage.outputTokens)} out
          {view.durationMs !== null ? ` · ${formatDuration(view.durationMs)}` : ''}
        </div>
      ) : null}
    </section>
  )
}
