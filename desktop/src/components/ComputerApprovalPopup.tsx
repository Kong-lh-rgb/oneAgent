/** 电脑审批共享逻辑：分类 + FIFO 队列 hook + 浮动卡片（供独立浮窗使用）。

分类/文案/队列的纯逻辑在 ``../approval/computerApproval``；
本模块只做 React 封装。
*/

import { useCallback, useEffect, useMemo, useReducer } from 'react'

import { approveApproval, denyApproval, listApprovals } from '../api/approvals'
import type { ApprovalRequest } from '../api/types'
import { connectComputerApprovalStream } from '../approval/computerApprovalStream'
import {
  initialFloatingApprovalState,
  reduceFloatingApprovalState,
  type FloatingApprovalPhase,
} from '../approval/floatingApprovalState'
import { rpcClient } from '../rpc'
import {
  computerActionDescription,
  computerActionLabel,
  computerActionSummary,
} from '../approval/computerApproval'

export {
  isComputerApproval,
  isDesktopApproval,
  isSandboxApproval,
  isChatApproval,
  chatShouldShowApproval,
  floatingShouldShowApproval,
  computerActionLabel,
  computerActionDescription,
  computerActionSummary,
  formatKeyShortcut,
} from '../approval/computerApproval'

export interface ApprovalQueueState {
  queue: ApprovalRequest[]
  active: ApprovalRequest | null
  busy: boolean
  error: string | null
  phase: FloatingApprovalPhase
  resolve: (decision: 'approve' | 'deny') => Promise<void>
  dismiss: () => void
}

/** 订阅审批事件，维护电脑审批 FIFO 队列（仅 computer_*）。 */
export function useComputerApprovalQueue(): ApprovalQueueState {
  const [state, dispatch] = useReducer(
    reduceFloatingApprovalState,
    initialFloatingApprovalState,
  )

  // 浮窗是独立 Renderer，必须自行维护 RPC 连接；重连后重新同步 pending。
  useEffect(() => {
    let cancelled = false
    const disconnect = connectComputerApprovalStream(rpcClient, {
      syncPending: async () => {
        const approvals = await listApprovals('pending')
        if (cancelled) return
        dispatch({ type: 'sync_pending', approvals })
      },
      onRequired: (approval) =>
        dispatch({ type: 'approval_required', approval }),
      onResolved: (approval) =>
        dispatch({ type: 'approval_resolved', approval }),
      onAgentEvent: (event) => dispatch({ type: 'agent_event', event }),
      onRunStatus: (runId, status) =>
        dispatch({ type: 'run_status', runId, status }),
      onConnectionError: () => {
        // RpcClient 自动重连；恢复后再次同步，不弹出没有审批内容的错误空窗。
      },
    })
    return () => {
      cancelled = true
      disconnect()
    }
  }, [])

  const resolve = useCallback(
    async (decision: 'approve' | 'deny'): Promise<void> => {
      if (!state.current) return
      dispatch({ type: 'submit_started' })
      try {
        const approval =
          decision === 'approve'
            ? await approveApproval(state.current.id)
            : await denyApproval(state.current.id)
        // approval resolved 只进入 executing/denied；真正完成等待 tool_completed。
        dispatch({ type: 'approval_resolved', approval })
      } catch (err) {
        dispatch({
          type: 'submit_failed',
          error: err instanceof Error ? err.message : String(err),
        })
      }
    },
    [state.current],
  )

  const queue = useMemo(
    () => (state.current ? [state.current, ...state.queue] : state.queue),
    [state.current, state.queue],
  )
  const dismiss = useCallback(() => dispatch({ type: 'dismiss' }), [])

  return {
    queue,
    active: state.current,
    busy: state.phase === 'submitting',
    error: state.error,
    phase: state.phase,
    resolve,
    dismiss,
  }
}

export interface ApprovalFloatingCardProps {
  approval: ApprovalRequest
  queuedCount?: number
  busy?: boolean
  error?: string | null
  phase?: FloatingApprovalPhase
  onApprove: (id: string) => void
  onDeny: (id: string) => void
}

/** 把审批 arguments 序列化成可供 Show details 展示的 JSON。 */
function formatArgumentsJson(argumentsValue: Record<string, unknown>): string {
  try {
    return JSON.stringify(argumentsValue, null, 2)
  } catch {
    return String(argumentsValue)
  }
}

/**
 * 浮窗内 macOS permission-panel 风格的展示卡片。
 * 主区域只放用户能看懂的动作与参数摘要；技术细节收进 Show details。
 */
export function ApprovalFloatingCard({
  approval,
  queuedCount = 0,
  busy = false,
  error = null,
  phase = 'pending',
  onApprove,
  onDeny,
}: ApprovalFloatingCardProps): React.JSX.Element {
  const label = computerActionLabel(approval)
  const description = computerActionDescription(approval)
  const summary = computerActionSummary(approval)
  const argsText = formatArgumentsJson(approval.arguments)
  const techLine = [
    approval.tool_name,
    approval.run_id ? `run: ${approval.run_id.slice(0, 8)}` : null,
  ]
    .filter(Boolean)
    .join(' · ')
  const presentation = floatingApprovalPresentation(phase, label, error)
  const canDecide = phase === 'pending' || phase === 'rpc_error'
  const showProgress = !canDecide

  return (
    <div className="floating-approval" data-testid="approval-floating-card">
      <header className="floating-approval__bar">
        <span className="floating-approval__brand">
          <span className="floating-approval__dot" aria-hidden="true" />
          Vesta
        </span>
        {queuedCount > 1 ? (
          <span className="floating-approval__waiting">
            {queuedCount - 1} more waiting
          </span>
        ) : null}
      </header>

      <div className="floating-approval__body">
        <div className="floating-approval__eyebrow">{presentation.eyebrow}</div>
        <h2 className="floating-approval__title">{presentation.title}</h2>
        <p className="floating-approval__desc">
          {phase === 'pending' || phase === 'rpc_error'
            ? description
            : presentation.description}
        </p>
        {showProgress ? (
          <div className={`floating-approval__status floating-approval__status--${presentation.tone}`}>
            <span className="floating-approval__status-dot" aria-hidden="true" />
            {presentation.status}
          </div>
        ) : null}
        {summary ? (
          <div className="floating-approval__summary">{summary}</div>
        ) : null}

        <details className="floating-approval__details">
          <summary>Show details</summary>
          {techLine ? (
            <div className="floating-approval__tech">{techLine}</div>
          ) : null}
          <pre className="floating-approval__json">{argsText}</pre>
        </details>

        {error ? (
          <div className="error-text floating-approval__error">{error}</div>
        ) : null}
      </div>

      {canDecide ? <footer className="floating-approval__actions">
        <button
          type="button"
          className="floating-approval__btn floating-approval__btn--deny"
          disabled={busy}
          onClick={() => onDeny(approval.id)}
          aria-label={`Deny ${label}`}
        >
          Deny
        </button>
        <button
          type="button"
          className="floating-approval__btn floating-approval__btn--allow"
          disabled={busy}
          onClick={() => onApprove(approval.id)}
          aria-label={`Allow ${label}`}
        >
          Allow
        </button>
      </footer> : null}
    </div>
  )
}

interface FloatingApprovalPresentation {
  eyebrow: string
  title: string
  description: string
  status: string
  tone: 'working' | 'success' | 'danger' | 'neutral'
}

/** 浮窗状态的人类可读文案；“投递成功”不会冒充“界面效果已验证”。 */
export function floatingApprovalPresentation(
  phase: FloatingApprovalPhase,
  actionLabel: string,
  error: string | null,
): FloatingApprovalPresentation {
  switch (phase) {
    case 'submitting':
      return { eyebrow: 'Approval received', title: actionLabel, description: 'Sending your decision…', status: 'Submitting approval', tone: 'working' }
    case 'executing':
      return { eyebrow: 'Approved', title: actionLabel, description: 'Vesta is performing the approved computer action.', status: 'Executing', tone: 'working' }
    case 'action_delivered':
      return { eyebrow: 'Action sent', title: actionLabel, description: 'The command was delivered. Vesta is checking the result.', status: 'Checking the result', tone: 'working' }
    case 'action_failed':
      return { eyebrow: 'Action failed', title: actionLabel, description: error ?? 'The computer action could not be completed.', status: 'Vesta is deciding what to do next', tone: 'danger' }
    case 'continuing':
      return { eyebrow: 'Working', title: 'Vesta is continuing', description: 'The approved action finished and the agent is continuing the run.', status: 'Agent running', tone: 'working' }
    case 'run_completed':
      return { eyebrow: 'Done', title: 'Run completed', description: 'Vesta finished the task.', status: 'Completed', tone: 'success' }
    case 'run_failed':
      return { eyebrow: 'Stopped', title: 'Run stopped', description: error ?? 'The run could not continue.', status: 'Needs attention', tone: 'danger' }
    case 'denied':
      return { eyebrow: 'Denied', title: actionLabel, description: 'The computer action was not allowed.', status: 'Action denied', tone: 'neutral' }
    case 'rpc_error':
    case 'pending':
      return { eyebrow: 'Permission required', title: actionLabel, description: '', status: '', tone: 'neutral' }
  }
}
