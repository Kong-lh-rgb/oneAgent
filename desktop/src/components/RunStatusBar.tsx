/** Run Status Bar：Agent Command Workspace 的顶部状态条。

不再是「conversation title + Activity」的聊天页头，而是当前 Run 的主状态视觉：
- 左侧：Vesta、当前会话或任务标题、当前动作。
- 中间：运行阶段、模式、步骤、操作数、用量和耗时。
- 右侧：停止、恢复和详情入口。
- 失败或中断时：展示中文原因、统计数据和可用操作。
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
  pending: { label: '等待开始', tone: 'waiting' },
  running: { label: '正在执行', tone: 'working' },
  completed: { label: '已完成', tone: 'completed' },
  failed: { label: '已停止', tone: 'failed' },
  cancelled: { label: '已取消', tone: 'cancelled' },
  interrupted: { label: '已中断', tone: 'failed' },
}

const STOP_REASON_LABEL: Record<string, string> = {
  max_steps: '已达到最大执行步数',
  model_error: '模型调用失败',
  repeated_tool_call: '重复调用了相同工具',
  stale_observation: '电脑画面状态已经变化',
  permission_denied: '操作未获得批准',
  context_error: '上下文超过可用窗口',
  cancelled: '已由用户取消',
  interrupted: '执行已中断',
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
    ? { label: '等待确认', tone: 'waiting' }
    : turnState === 'verifying'
      ? { label: '正在验证', tone: 'waiting' }
      : turnState === 'thinking'
        ? { label: '正在分析', tone: 'working' }
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
        title={conversationSidebarOpen ? '收起会话列表' : '展开会话列表'}
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
          {status?.label ?? '就绪'}
        </span>
        {mode === 'plan' ? <span>规划模式</span> : null}
        {step !== null && step !== undefined && step > 0 ? (
          <span>第 {step} 步</span>
        ) : null}
        {toolCount ? <span>{toolCount} 次操作</span> : null}
        {totalTokens ? <span>用量 {formatTokens(totalTokens)} Token</span> : null}
        {elapsed !== null ? <span>{formatDuration(elapsed)}</span> : null}
      </div>

      <div className="run-status-bar__actions">
        {running ? (
          <button type="button" className="btn btn-sm btn-danger" onClick={onStop}>
            停止
          </button>
        ) : null}
        {runStatus === 'interrupted' ? (
          <button type="button" className="btn btn-sm" onClick={onRecover}>
            恢复
          </button>
        ) : null}
        <button
          type="button"
          className={`header-action ${activityOpen ? 'active' : ''}`}
          onClick={onToggleActivity}
          aria-pressed={activityOpen}
          title="运行详情、上下文与执行轨迹"
        >
          <Icon name="activity" size={14} />
          详情
        </button>
      </div>

      {stopped ? (
        <div className="run-status-bar__failed">
          <span className="run-status-bar__failed-dot" aria-hidden="true" />
          已停止 · {reasonLabel}
          {step || toolCount || totalTokens ? (
            <span className="run-status-bar__failed-stats">
              {step ? `${step} 步 · ` : ''}
              {toolCount ? `${toolCount} 次操作 · ` : ''}
              {totalTokens ? `用量 ${formatTokens(totalTokens)} Token` : ''}
            </span>
          ) : null}
        </div>
      ) : null}
    </header>
  )
}
