/** Desktop 审批浮窗的纯状态机：审批、动作执行与 Run 终态。 */

import type { AgentEvent, ApprovalRequest } from '../api/types'
import { isDesktopApproval, pushApproval, removeApproval } from './computerApproval'

export type FloatingApprovalPhase =
  | 'pending'
  | 'submitting'
  | 'executing'
  | 'action_delivered'
  | 'action_failed'
  | 'continuing'
  | 'run_completed'
  | 'run_failed'
  | 'denied'
  | 'rpc_error'

export interface FloatingApprovalState {
  current: ApprovalRequest | null
  queue: ApprovalRequest[]
  phase: FloatingApprovalPhase
  error: string | null
}

export type FloatingApprovalEvent =
  | { type: 'sync_pending'; approvals: ApprovalRequest[] }
  | { type: 'approval_required'; approval: ApprovalRequest }
  | { type: 'approval_resolved'; approval: ApprovalRequest }
  | { type: 'submit_started' }
  | { type: 'submit_failed'; error: string }
  | { type: 'agent_event'; event: AgentEvent }
  | { type: 'run_status'; runId: string; status: string }
  | { type: 'dismiss' }

export const initialFloatingApprovalState: FloatingApprovalState = {
  current: null,
  queue: [],
  phase: 'pending',
  error: null,
}

function startApproval(
  approval: ApprovalRequest,
  queue: ApprovalRequest[] = [],
): FloatingApprovalState {
  return {
    current: approval,
    queue: queue.filter((item) => item.id !== approval.id),
    phase: 'pending',
    error: null,
  }
}

function advanceQueue(state: FloatingApprovalState): FloatingApprovalState {
  const [next, ...rest] = state.queue
  return next ? startApproval(next, rest) : initialFloatingApprovalState
}

function isWaitingForDecision(phase: FloatingApprovalPhase): boolean {
  return phase === 'pending' || phase === 'submitting' || phase === 'rpc_error'
}

function eventError(event: AgentEvent): string | null {
  const raw = event.error
  if (raw && typeof raw === 'object' && 'message' in raw) {
    const message = (raw as { message?: unknown }).message
    if (typeof message === 'string' && message) return message
  }
  if (event.message?.content) return event.message.content
  return event.stop_reason ?? null
}

/** 所有 UI 状态转换集中在这里，React hook 只负责接线与 RPC 副作用。 */
export function reduceFloatingApprovalState(
  state: FloatingApprovalState,
  event: FloatingApprovalEvent,
): FloatingApprovalState {
  switch (event.type) {
    case 'sync_pending': {
      const pending = event.approvals.filter(isDesktopApproval)
      if (!state.current || isWaitingForDecision(state.phase)) {
        const [current, ...queue] = pending
        return current ? startApproval(current, queue) : initialFloatingApprovalState
      }
      let queue = state.queue
      for (const approval of pending) {
        if (approval.id !== state.current.id) queue = pushApproval(queue, approval)
      }
      return { ...state, queue }
    }
    case 'approval_required': {
      const approval = event.approval
      if (!isDesktopApproval(approval)) return state
      if (!state.current) return startApproval(approval)
      if (state.current.id === approval.id) return state
      // Agent 继续阶段遇到下一次审批时，立即切回可操作的审批卡片。
      if (
        state.phase === 'continuing' ||
        state.phase === 'action_delivered' ||
        state.phase === 'action_failed' ||
        state.phase === 'run_completed' ||
        state.phase === 'run_failed' ||
        state.phase === 'denied'
      ) {
        return startApproval(approval, state.queue)
      }
      return { ...state, queue: pushApproval(state.queue, approval) }
    }
    case 'approval_resolved': {
      const approval = event.approval
      if (state.current?.id !== approval.id) {
        return { ...state, queue: removeApproval(state.queue, approval.id) }
      }
      // RPC response可能晚于 tool_completed / run.status 到达，不能把较新的
      // 执行或终态倒退回 executing。
      if (
        approval.status === 'approved' &&
        !isWaitingForDecision(state.phase) &&
        state.phase !== 'executing'
      ) {
        return { ...state, current: approval }
      }
      return {
        ...state,
        current: approval,
        phase: approval.status === 'approved' ? 'executing' : 'denied',
        error: null,
      }
    }
    case 'submit_started':
      if (!state.current || !isWaitingForDecision(state.phase)) return state
      return { ...state, phase: 'submitting', error: null }
    case 'submit_failed':
      if (!state.current) return state
      return { ...state, phase: 'rpc_error', error: event.error }
    case 'agent_event': {
      if (!state.current || event.event.run_id !== state.current.run_id) return state
      const agentEvent = event.event
      const callId = state.current.tool_call_id
      if (
        agentEvent.type === 'tool_started' &&
        agentEvent.tool_call?.id === callId
      ) {
        return { ...state, phase: 'executing', error: null }
      }
      if (
        agentEvent.type === 'tool_completed' &&
        agentEvent.tool_result?.tool_call_id === callId
      ) {
        return agentEvent.tool_result.success
          ? { ...state, phase: 'action_delivered', error: null }
          : {
              ...state,
              phase: 'action_failed',
              error: agentEvent.tool_result.error ?? 'Computer action failed',
            }
      }
      if (
        agentEvent.type === 'model_started' &&
        (state.phase === 'action_delivered' || state.phase === 'action_failed')
      ) {
        return { ...state, phase: 'continuing' }
      }
      if (agentEvent.type === 'agent_completed') {
        return { ...state, phase: 'run_completed', error: null }
      }
      if (agentEvent.type === 'agent_failed') {
        return {
          ...state,
          phase: 'run_failed',
          error: eventError(agentEvent) ?? 'Agent run failed',
        }
      }
      return state
    }
    case 'run_status':
      if (!state.current || event.runId !== state.current.run_id) return state
      if (event.status === 'completed') {
        return { ...state, phase: 'run_completed', error: null }
      }
      if (['failed', 'cancelled', 'interrupted'].includes(event.status)) {
        return {
          ...state,
          phase: 'run_failed',
          error: state.error ?? `Run ${event.status}`,
        }
      }
      return state
    case 'dismiss':
      return advanceQueue(state)
  }
}

/** 浮窗终态的自动隐藏时间；null 表示继续等待事件。 */
export function floatingApprovalDismissDelay(
  phase: FloatingApprovalPhase,
): number | null {
  if (phase === 'run_completed' || phase === 'denied') return 1500
  if (phase === 'run_failed') return 7000
  return null
}
