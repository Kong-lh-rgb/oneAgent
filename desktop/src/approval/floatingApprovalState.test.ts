/** Desktop 审批浮窗状态机测试。 */

import { describe, expect, it } from 'vitest'

import type { AgentEvent, ApprovalRequest } from '../api/types'
import {
  floatingApprovalDismissDelay,
  floatingApprovalShouldBeVisible,
  initialFloatingApprovalState,
  reduceFloatingApprovalState,
} from './floatingApprovalState'

function approval(id: string, overrides: Partial<ApprovalRequest> = {}): ApprovalRequest {
  return {
    id,
    run_id: 'run-1',
    conversation_id: 'conv-1',
    tool_name: 'computer_type',
    tool_call_id: `call-${id}`,
    arguments: { text: '测试' },
    reason: '',
    ui_scope: 'desktop',
    status: 'pending',
    created_at: '2026-08-21T00:00:00+00:00',
    resolved_at: null,
    ...overrides,
  }
}

function agentEvent(
  type: string,
  overrides: Partial<AgentEvent> = {},
): AgentEvent {
  return {
    event_id: `event-${type}`,
    run_id: 'run-1',
    conversation_id: 'conv-1',
    sequence: 1,
    type,
    event_time: '2026-08-21T00:00:01+00:00',
    step: 1,
    provider: null,
    model: null,
    message: null,
    tool_call: null,
    tool_result: null,
    usage: null,
    stop_reason: null,
    approval_decision: null,
    ...overrides,
  }
}

describe('reduceFloatingApprovalState', () => {
  it('只接收 desktop pending，并按 FIFO 建立当前项与队列', () => {
    const a = approval('a')
    const b = approval('b')
    const sandbox = approval('s', {
      tool_name: 'run_shell_command',
      ui_scope: 'sandbox',
    })
    const state = reduceFloatingApprovalState(initialFloatingApprovalState, {
      type: 'sync_pending',
      approvals: [a, sandbox, b],
    })
    expect(state.current?.id).toBe('a')
    expect(state.queue.map((item) => item.id)).toEqual(['b'])
    expect(state.phase).toBe('pending')
  })

  it('批准只进入 executing，不会在 approval.resolved 时隐藏', () => {
    const pending = reduceFloatingApprovalState(initialFloatingApprovalState, {
      type: 'approval_required',
      approval: approval('a'),
    })
    const submitting = reduceFloatingApprovalState(pending, {
      type: 'submit_started',
    })
    const executing = reduceFloatingApprovalState(submitting, {
      type: 'approval_resolved',
      approval: approval('a', { status: 'approved' }),
    })
    expect(executing.current?.id).toBe('a')
    expect(executing.phase).toBe('executing')
  })

  it('匹配 tool_call_id：投递 → 继续 → Run 完成', () => {
    let state = reduceFloatingApprovalState(initialFloatingApprovalState, {
      type: 'approval_required',
      approval: approval('a'),
    })
    state = reduceFloatingApprovalState(state, {
      type: 'approval_resolved',
      approval: approval('a', { status: 'approved' }),
    })
    state = reduceFloatingApprovalState(state, {
      type: 'agent_event',
      event: agentEvent('tool_completed', {
        tool_result: {
          tool_call_id: 'call-a',
          tool_name: 'computer_type',
          success: true,
          output: '{"characters":2}',
          error: null,
          duration_ms: 20,
        },
      }),
    })
    expect(state.phase).toBe('action_delivered')

    state = reduceFloatingApprovalState(state, {
      type: 'agent_event',
      event: agentEvent('model_started'),
    })
    expect(state.phase).toBe('continuing')

    state = reduceFloatingApprovalState(state, {
      type: 'run_status',
      runId: 'run-1',
      status: 'completed',
    })
    expect(state.phase).toBe('run_completed')
  })

  it('迟到的 approval RPC 响应不能把动作终态倒退回 executing', () => {
    const current = approval('a', { status: 'approved' })
    const delivered = {
      ...initialFloatingApprovalState,
      current,
      phase: 'action_delivered' as const,
    }
    const afterLateResponse = reduceFloatingApprovalState(delivered, {
      type: 'approval_resolved',
      approval: current,
    })
    expect(afterLateResponse.phase).toBe('action_delivered')
  })

  it('工具失败显示原因，但后续模型继续时进入 continuing', () => {
    let state = reduceFloatingApprovalState(initialFloatingApprovalState, {
      type: 'approval_required',
      approval: approval('a'),
    })
    state = reduceFloatingApprovalState(state, {
      type: 'agent_event',
      event: agentEvent('tool_completed', {
        tool_result: {
          tool_call_id: 'call-a',
          tool_name: 'computer_type',
          success: false,
          output: null,
          error: 'fresh observation required',
          duration_ms: 2,
        },
      }),
    })
    expect(state.phase).toBe('action_failed')
    expect(state.error).toContain('fresh observation')

    state = reduceFloatingApprovalState(state, {
      type: 'agent_event',
      event: agentEvent('model_started'),
    })
    expect(state.phase).toBe('continuing')
  })

  it('忽略其它 Run 和其它 tool_call 的事件', () => {
    const state = reduceFloatingApprovalState(initialFloatingApprovalState, {
      type: 'approval_required',
      approval: approval('a'),
    })
    const unrelated = reduceFloatingApprovalState(state, {
      type: 'agent_event',
      event: agentEvent('tool_completed', {
        tool_result: {
          tool_call_id: 'call-other',
          tool_name: 'computer_type',
          success: true,
          output: null,
          error: null,
          duration_ms: 1,
        },
      }),
    })
    expect(unrelated).toBe(state)
  })

  it('Agent 继续期间出现新审批时立即切回新的审批卡片', () => {
    let state = reduceFloatingApprovalState(initialFloatingApprovalState, {
      type: 'approval_required',
      approval: approval('a'),
    })
    state = { ...state, phase: 'continuing' }
    state = reduceFloatingApprovalState(state, {
      type: 'approval_required',
      approval: approval('b'),
    })
    expect(state.current?.id).toBe('b')
    expect(state.phase).toBe('pending')
  })

  it('终态 dismiss 后继续 FIFO 下一项', () => {
    const a = approval('a')
    const b = approval('b')
    let state = reduceFloatingApprovalState(initialFloatingApprovalState, {
      type: 'sync_pending',
      approvals: [a, b],
    })
    state = { ...state, phase: 'run_completed' }
    state = reduceFloatingApprovalState(state, { type: 'dismiss' })
    expect(state.current?.id).toBe('b')
    expect(state.phase).toBe('pending')
  })
})

describe('floatingApprovalDismissDelay', () => {
  it('完成/拒绝短暂停留，失败保留更久，其余等待事件', () => {
    expect(floatingApprovalDismissDelay('run_completed')).toBe(1500)
    expect(floatingApprovalDismissDelay('denied')).toBe(1500)
    expect(floatingApprovalDismissDelay('run_failed')).toBe(7000)
    expect(floatingApprovalDismissDelay('executing')).toBeNull()
  })
})

describe('floatingApprovalShouldBeVisible', () => {
  it('审批交互与终态可见', () => {
    expect(floatingApprovalShouldBeVisible('pending')).toBe(true)
    expect(floatingApprovalShouldBeVisible('submitting')).toBe(true)
    expect(floatingApprovalShouldBeVisible('rpc_error')).toBe(true)
    expect(floatingApprovalShouldBeVisible('denied')).toBe(true)
    expect(floatingApprovalShouldBeVisible('run_completed')).toBe(true)
    expect(floatingApprovalShouldBeVisible('run_failed')).toBe(true)
  })

  it('批准后的执行阶段隐藏，避免 Electron 抢回前台', () => {
    expect(floatingApprovalShouldBeVisible('executing')).toBe(false)
    expect(floatingApprovalShouldBeVisible('action_delivered')).toBe(false)
    expect(floatingApprovalShouldBeVisible('action_failed')).toBe(false)
    expect(floatingApprovalShouldBeVisible('continuing')).toBe(false)
  })
})
