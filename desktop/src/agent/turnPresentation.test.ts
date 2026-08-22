/** turnPresentation：Turn 展示层纯逻辑单测。 */

import { describe, expect, it } from 'vitest'

import type { AgentEvent } from '../api/types'
import {
  buildComputerContext,
  buildTurnView,
  formatDuration,
  formatTokens,
  humanizeToolName,
  parseVerificationStatus,
  toolActiveLabel,
  toolDoneLabel,
} from './turnPresentation'

function event(partial: Partial<AgentEvent>): AgentEvent {
  return {
    event_id: 'evt',
    run_id: 'run-1',
    conversation_id: 'conv-1',
    sequence: 1,
    type: 'agent_started',
    event_time: '2026-08-21T00:00:00.000Z',
    step: 1,
    provider: 'fake',
    model: 'fake',
    message: null,
    tool_call: null,
    tool_result: null,
    usage: null,
    stop_reason: null,
    approval_decision: null,
    delta: null,
    ...partial,
  }
}

const toolCall = (id: string, name: string, args: Record<string, unknown>) => ({
  id,
  name,
  arguments: args,
})

describe('buildTurnView: tool timeline', () => {
  it('合并 tool_started + tool_completed 为一条，并标为 done', () => {
    const view = buildTurnView([
      event({ type: 'agent_started', event_time: '2026-08-21T00:00:00.000Z' }),
      event({
        type: 'tool_started',
        tool_call: toolCall('t1', 'computer_type', { text: '测试' }),
      }),
      event({
        type: 'tool_completed',
        tool_call: { id: 't1', name: 'computer_type', arguments: {} },
        tool_result: { tool_call_id: 't1', tool_name: 'computer_type', success: true, output: 'ok', error: null, duration_ms: 10 },
      }),
      event({ type: 'agent_completed', event_time: '2026-08-21T00:00:10.000Z' }),
    ])
    expect(view.tools).toHaveLength(1)
    expect(view.tools[0].state).toBe('done')
    expect(view.tools[0].label).toBe('已输入 “测试”')
    expect(view.toolCount).toBe(1)
    expect(view.durationMs).toBe(10_000)
  })

  it('tool 失败标记 failed', () => {
    const view = buildTurnView([
      event({ type: 'tool_started', tool_call: toolCall('t1', 'computer_click', {}) }),
      event({
        type: 'tool_completed',
        tool_call: { id: 't1', name: 'computer_click', arguments: {} },
        tool_result: { tool_call_id: 't1', tool_name: 'computer_click', success: false, output: null, error: 'x', duration_ms: 5 },
      }),
    ])
    expect(view.tools[0].state).toBe('failed')
    expect(view.tools[0].label).toBe('点击失败')
  })

  it('toolCount 按 tool_call_id 去重', () => {
    const view = buildTurnView([
      event({ type: 'tool_started', tool_call: toolCall('t1', 'read_file', { path: '/a' }) }),
      event({ type: 'tool_started', tool_call: toolCall('t2', 'write_file', { path: '/b' }) }),
      event({ type: 'tool_started', tool_call: toolCall('t1', 'read_file', { path: '/a' }) }),
    ])
    expect(view.toolCount).toBe(2)
  })
})

describe('buildTurnView: approval', () => {
  it('approval_required 置 waiting，approval_completed approved 后回到 active', () => {
    const view = buildTurnView([
      event({ type: 'tool_started', tool_call: toolCall('t1', 'run_shell_command', {}) }),
      event({ type: 'tool_approval_required', tool_call: toolCall('t1', 'run_shell_command', {}) }),
      event({
        type: 'tool_approval_completed',
        tool_call: toolCall('t1', 'run_shell_command', {}),
        approval_decision: 'approved',
      }),
      event({
        type: 'tool_completed',
        tool_call: { id: 't1', name: 'run_shell_command', arguments: {} },
        tool_result: { tool_call_id: 't1', tool_name: 'run_shell_command', success: true, output: null, error: null, duration_ms: 1 },
      }),
    ])
    expect(view.tools[0].approval).toBe('approved')
    expect(view.tools[0].state).toBe('done')
  })

  it('denied 后状态 failed', () => {
    const view = buildTurnView([
      event({ type: 'tool_approval_required', tool_call: toolCall('t1', 'computer_type', {}) }),
      event({
        type: 'tool_approval_completed',
        tool_call: toolCall('t1', 'computer_type', {}),
        approval_decision: 'denied',
      }),
    ])
    expect(view.tools[0].approval).toBe('denied')
    expect(view.tools[0].state).toBe('failed')
  })
})

describe('buildTurnView: verification', () => {
  it('unverified computer 操作标记 unverified', () => {
    const view = buildTurnView([
      event({ type: 'tool_started', tool_call: toolCall('t1', 'computer_click', {}) }),
      event({
        type: 'tool_completed',
        tool_call: { id: 't1', name: 'computer_click', arguments: {} },
        tool_result: {
          tool_call_id: 't1',
          tool_name: 'computer_click',
          success: true,
          output: '{"delivery_status":"sent","verification_status":"unverified"}',
          error: null,
          duration_ms: 1,
        },
      }),
    ])
    expect(view.tools[0].verification).toBe('unverified')
  })

  it('observe 验证后把最近 unverified 操作标为 verified', () => {
    const view = buildTurnView([
      event({ type: 'tool_started', tool_call: toolCall('t1', 'computer_click', {}) }),
      event({
        type: 'tool_completed',
        tool_call: { id: 't1', name: 'computer_click', arguments: {} },
        tool_result: {
          tool_call_id: 't1',
          tool_name: 'computer_click',
          success: true,
          output: '{"verification_status":"unverified"}',
          error: null,
          duration_ms: 1,
        },
      }),
      event({ type: 'tool_started', tool_call: toolCall('t2', 'computer_observe', {}) }),
      event({
        type: 'tool_completed',
        tool_call: { id: 't2', name: 'computer_observe', arguments: {} },
        tool_result: {
          tool_call_id: 't2',
          tool_name: 'computer_observe',
          success: true,
          output: '{"frontmost_verified":true}',
          error: null,
          duration_ms: 1,
        },
      }),
    ])
    expect(view.tools.find((t) => t.id === 't1')?.verification).toBe('verified')
  })
})

describe('buildTurnView: usage / steps / duration', () => {
  it('running 时累计 model_completed usage', () => {
    const view = buildTurnView([
      event({ type: 'model_started', step: 1 }),
      event({ type: 'model_completed', step: 1, usage: { input_tokens: 500, output_tokens: 300, total_tokens: 800, cached_input_tokens: 400 } }),
      event({ type: 'model_started', step: 2 }),
      event({ type: 'model_completed', step: 2, usage: { input_tokens: 600, output_tokens: 320, total_tokens: 920, cached_input_tokens: 500 } }),
    ])
    expect(view.steps).toBe(2)
    expect(view.usage?.inputTokens).toBe(1100)
    expect(view.usage?.outputTokens).toBe(620)
    expect(view.usage?.totalTokens).toBe(1720)
    expect(view.usage?.cachedInputTokens).toBe(900)
    expect(view.usage?.cacheHitRate).toBeCloseTo(81.8, 1)
  })

  it('任一模型调用未报告缓存时保持命中率未知', () => {
    const view = buildTurnView([
      event({ type: 'model_completed', step: 1, usage: { input_tokens: 500, output_tokens: 20, total_tokens: 520, cached_input_tokens: 400 } }),
      event({ type: 'model_completed', step: 2, usage: { input_tokens: 600, output_tokens: 20, total_tokens: 620 } }),
    ])

    expect(view.usage?.cachedInputTokens).toBeNull()
    expect(view.usage?.cacheHitRate).toBeNull()
  })

  it('agent_completed result.usage 优先', () => {
    const view = buildTurnView([
      event({ type: 'model_completed', step: 1, usage: { input_tokens: 100, output_tokens: 100, total_tokens: 200 } }),
      event({
        type: 'agent_completed',
        result: {
          run_id: 'r',
          final_message: null as never,
          messages: [],
          steps: 4,
          stop_reason: 'final_answer',
          usage: { input_tokens: 9800, output_tokens: 620, total_tokens: 10420, cached_input_tokens: 7840 },
          error: null,
          plan_task_id: null,
        },
      }),
    ])
    expect(view.usage?.inputTokens).toBe(9800)
    expect(view.usage?.outputTokens).toBe(620)
    expect(view.usage?.cacheHitRate).toBe(80)
    expect(view.steps).toBe(4)
    expect(view.status).toBe('completed')
  })

  it('duration 由 agent_started → 结束事件计算', () => {
    const view = buildTurnView([
      event({ type: 'agent_started', event_time: '2026-08-21T00:00:00.000Z' }),
      event({ type: 'agent_completed', event_time: '2026-08-21T00:00:18.400Z' }),
    ])
    expect(view.durationMs).toBe(18_400)
  })
})

describe('reasoning 协议不展示 / 不解析', () => {
  it('buildTurnView 忽略历史 reasoning 事件，只保留结构化过程', () => {
    const view = buildTurnView([
      event({ type: 'model_reasoning_delta', step: 1, reasoning_delta: '<tool_calls>' }),
      event({ type: 'model_started', step: 1 }),
    ])
    expect(view.tools).toHaveLength(0)
    expect(view.steps).toBe(1)
  })
})

describe('label helpers', () => {
  it('humanizeToolName 把下划线转空格并去掉 mcp 前缀', () => {
    expect(humanizeToolName('run_shell_command')).toBe('run shell command')
    expect(humanizeToolName('mcp__files__read')).toBe('read')
  })

  it('toolActiveLabel 带参数摘要', () => {
    expect(toolActiveLabel('computer_type', { text: '测试' })).toBe('输入 “测试”')
    expect(toolActiveLabel('read_file', { path: '/tmp/a.md' })).toBe('读取 /tmp/a.md')
    expect(toolActiveLabel('computer_key', { key: 'n', modifiers: 'command' })).toBe('按键 ⌘ N')
    expect(toolActiveLabel('unknown_tool', {})).toBe('运行 unknown tool')
  })

  it('toolDoneLabel 完成/失败', () => {
    expect(toolDoneLabel('computer_type', { text: 'x' }, true)).toBe('已输入 “x”')
    expect(toolDoneLabel('computer_type', { text: 'x' }, false)).toBe('输入失败')
  })
})

describe('parseVerificationStatus', () => {
  it('解析 verified / unverified / frontmost_verified', () => {
    expect(parseVerificationStatus('{"verification_status":"verified"}')).toBe('verified')
    expect(parseVerificationStatus('{"verification_status":"unverified"}')).toBe('unverified')
    expect(parseVerificationStatus('{"frontmost_verified":true}')).toBe('verified')
    expect(parseVerificationStatus('hello')).toBeNull()
    expect(parseVerificationStatus(null)).toBeNull()
  })
})

describe('buildComputerContext', () => {
  it('组合 Observation target、窗口、最近动作和验证状态', () => {
    const events = [
      event({
        type: 'tool_started',
        tool_call: toolCall('t1', 'computer_type', { text: ' Vesta' }),
      }),
      event({
        type: 'tool_completed',
        tool_call: toolCall('t1', 'computer_type', {}),
        tool_result: {
          tool_call_id: 't1',
          tool_name: 'computer_type',
          success: true,
          output: '{"verification_status":"verified","execution_mode":"background_ax"}',
          error: null,
          duration_ms: 20,
        },
      }),
    ]
    const context = buildComputerContext(events, {
      id: 'snapshot-1',
      created_at: null,
      active_app: { name: 'Vesta', bundle_id: null, pid: 1 },
      target: { name: 'TextEdit', bundle_id: 'com.apple.TextEdit', pid: 2 },
      active_window: {
        ref: 'w1', title: 'Untitled', bounds: { x: 0, y: 0, width: 1, height: 1 },
      },
      windows: [],
      elements: [],
      screenshot_ref: null,
    })
    expect(context.target).toBe('TextEdit')
    expect(context.window).toBe('Untitled')
    expect(context.lastAction).toBe('已输入 “ Vesta”')
    expect(context.verification).toBe('已验证')
    expect(context.executionMode).toBe('background ax')
  })

  it('没有 Session 证据时不虚构目标', () => {
    expect(buildComputerContext([], null)).toMatchObject({
      target: null,
      window: null,
      lastAction: null,
      verification: null,
      recentActions: [],
    })
  })
})

describe('format helpers', () => {
  it('formatTokens', () => {
    expect(formatTokens(980)).toBe('980')
    expect(formatTokens(1234)).toBe('1.2k')
    expect(formatTokens(18400)).toBe('18.4k')
  })
  it('formatDuration', () => {
    expect(formatDuration(18_400)).toBe('18.4s')
    expect(formatDuration(60_500)).toBe('1m 01s')
    expect(formatDuration(null)).toBe('')
  })
})
