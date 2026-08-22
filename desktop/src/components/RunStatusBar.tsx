/** Run Status Bar：Agent Command Workspace 的顶部状态条。

不再是「conversation title + Activity」的聊天页头，而是当前 Run 的主状态视觉：
- 左侧：Vesta · 当前 conversation / 任务标题 + 当前动作（Working · Step N）
- 中间：Normal/Plan · Step · tools · tokens · duration（运行中实时跳秒）
- 右侧：Stop / Recover / Activity
- 失败/中断：inline Stopped + reason + 统计 + [Recover] [Activity]
*/

import { useEffect, useState } from 'react'
import type { ReactElement } from 'react'

import type { AgentMode } from '../api/types'
import {
  formatDuration,
  formatTokens,
  humanizeRunError,
  type TurnView,
} from '../agent/turnPresentation'
import { Icon } from './Icon'

const STATUS_LABEL: Record<string, { label: string; tone: string }> = {
  pending: { label: 'Waiting', tone: 'waiting' },
  running: { label: 'Working', tone: 'working' },
  completed: { label: 'Completed', tone: 'completed' },
  failed: { label: 'Stopped', tone: 'failed' },
  cancelled: { label: 'Cancelled', tone: 'cancelled' },
  interrupted: { label: 'Stopped', tone: 'failed' },
}

const STOP_REASON_LABEL: Record<string, string> = {
  max_steps: 'Maximum step limit reached',
  model_error: 'Model error',
  repeated_tool_call: 'Repeated tool call',
  stale_observation: 'Stale observation',
  permission_denied: 'Permission denied',
  context_error: 'Context window exceeded',
  cancelled: 'Cancelled by user',
  interrupted: 'Run interrupted',
}

export default function RunStatusBar({
  title,
  conversationSidebarOpen,
  onToggleConversationSidebar,
  runStatus,
  step,
  toolCount,
  totalTokens,
  durationMs,
  startedAt,
  currentAction,
  stopReason,
  mode,
  turnState,
  activityOpen,
  onToggleActivity,
  onStop,
  onRecover,
}: {
  title: string
  conversationSidebarOpen: boolean
  onToggleConversationSidebar: () => void
  runStatus?: string
  step?: number | null
  toolCount?: number
  totalTokens?: number | null
  durationMs?: number | null
  startedAt?: number | null
  currentAction?: string | null
  stopReason?: string | null
  mode?: AgentMode
  turnState?: TurnView['status']
  activityOpen: boolean
  onToggleActivity: () => void
  onStop?: () => void
  onRecover?: () => void
}): ReactElement {
  const turnLabel = turnState === 'waiting_approval'
    ? { label: 'Approval required', tone: 'waiting' }
    : turnState === 'verifying'
      ? { label: 'Verifying', tone: 'waiting' }
      : turnState === 'thinking'
        ? { label: 'Thinking', tone: 'working' }
        : undefined
  const status = turnLabel ?? (runStatus ? STATUS_LABEL[runStatus] : undefined)
  const running = runStatus === 'running'
  const stopped = runStatus === 'failed' || runStatus === 'interrupted'
  const reasonLabel = stopReason && STOP_REASON_LABEL[stopReason]
    ? STOP_REASON_LABEL[stopReason]
    : humanizeRunError(stopReason ?? null).message

  // 运行中 duration 每秒跳一次（起始时间来自 agent_started 事件）。
  const [elapsed, setElapsed] = useState<number | null>(
    durationMs ?? null,
  )
  useEffect(() => {
    if (!running) {
      setElapsed(durationMs ?? null)
      return
    }
    const tick = (): void => {
      if (startedAt !== null && startedAt !== undefined) {
        setElapsed(Date.now() - startedAt)
      }
    }
    tick()
    const timer = window.setInterval(tick, 1000)
    return () => window.clearInterval(timer)
  }, [running, startedAt, durationMs])

  return (
    <header className="run-status-bar">
      <button
        type="button"
        className="icon-btn"
        onClick={onToggleConversationSidebar}
        aria-label={conversationSidebarOpen ? '收起会话列表' : '展开会话列表'}
        title={conversationSidebarOpen ? 'Hide conversations' : 'Show conversations'}
      >
        <Icon name={conversationSidebarOpen ? 'panelClose' : 'panelOpen'} />
      </button>

      <div className="run-status-bar__identity">
        <span className="run-status-bar__brand">Vesta</span>
        <span className="run-status-bar__title">{title}</span>
        {running && currentAction ? (
          <span className="run-status-bar__action">· {currentAction}</span>
        ) : null}
      </div>

      <div className="run-status-bar__stats">
        <span
          className={`run-status-bar__status run-status-bar__status--${status?.tone ?? ''}`}
        >
          <span className="run-status-bar__dot" aria-hidden="true" />
          {status?.label ?? 'Ready'}
        </span>
        {mode === 'plan' ? <span>Plan</span> : null}
        {step !== null && step !== undefined && step > 0 ? (
          <span>Step {step}</span>
        ) : null}
        {toolCount ? <span>{toolCount} tool{toolCount === 1 ? '' : 's'}</span> : null}
        {totalTokens ? <span>{formatTokens(totalTokens)} tokens</span> : null}
        {elapsed !== null ? <span>{formatDuration(elapsed)}</span> : null}
      </div>

      <div className="run-status-bar__actions">
        {running ? (
          <button type="button" className="btn btn-sm btn-danger" onClick={onStop}>
            Stop
          </button>
        ) : null}
        {runStatus === 'interrupted' ? (
          <button type="button" className="btn btn-sm" onClick={onRecover}>
            Recover
          </button>
        ) : null}
        <button
          type="button"
          className={`header-action ${activityOpen ? 'active' : ''}`}
          onClick={onToggleActivity}
          aria-pressed={activityOpen}
          title="Run 详情、Context 与 Trace"
        >
          <Icon name="activity" size={14} />
          详情
        </button>
      </div>

      {stopped ? (
        <div className="run-status-bar__failed">
          <span className="run-status-bar__failed-dot" aria-hidden="true" />
          Stopped · {reasonLabel}
          {step || toolCount || totalTokens ? (
            <span className="run-status-bar__failed-stats">
              {step ? `${step} steps · ` : ''}
              {toolCount ? `${toolCount} tools · ` : ''}
              {totalTokens ? `${formatTokens(totalTokens)} tokens` : ''}
            </span>
          ) : null}
        </div>
      ) : null}
    </header>
  )
}
