/** events store：流式增量批量提交（不丢字 / 立即 flush / run·step 不串流）。 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { handlers, statusListeners } = vi.hoisted(() => ({
  handlers: new Map<string, Set<(params: unknown) => void>>(),
  statusListeners: new Set<(connected: boolean) => void>(),
}))

vi.mock('../rpc', () => ({
  rpcClient: {
    on: (event: string, handler: (params: unknown) => void) => {
      let set = handlers.get(event)
      if (!set) {
        set = new Set()
        handlers.set(event, set)
      }
      set.add(handler)
      return () => set.delete(handler)
    },
    setStatusListener: (cb: (connected: boolean) => void) => {
      statusListeners.add(cb)
      return () => statusListeners.delete(cb)
    },
    connect: vi.fn(),
    disconnect: vi.fn(),
  },
}))

import { useEventsStore } from './events'

function emitAgentEvent(params: Record<string, unknown>): void {
  const set = handlers.get('agent.event')
  set?.forEach((handler) => handler(params))
}

function textDelta(runId: string, step: number, delta: string): Record<string, unknown> {
  return { type: 'model_output_delta', event_id: `evt-${Math.random()}`, run_id: runId, step, delta }
}

function reasoningDelta(runId: string, step: number, delta: string): Record<string, unknown> {
  return { type: 'model_reasoning_delta', event_id: `evt-${Math.random()}`, run_id: runId, step, reasoning_delta: delta }
}

function structured(type: string, runId = 'r1'): Record<string, unknown> {
  return { type, event_id: `evt-${Math.random()}`, run_id: runId, step: 1 }
}

describe('events store batching', () => {
  beforeEach(() => {
    // 清掉可能残留的模块级 flushTimer（disconnect 内部会 flushNow）。
    useEventsStore.getState().disconnect()
    useEventsStore.setState({
      connected: false,
      eventsByRun: {},
      streamTextByRun: {},
      reasoningByRun: {},
      runStatuses: {},
    })
    vi.useFakeTimers()
    useEventsStore.getState().connect()
  })

  afterEach(() => {
    vi.runAllTimers()
    vi.useRealTimers()
    useEventsStore.getState().disconnect()
    useEventsStore.setState({
      connected: false,
      eventsByRun: {},
      streamTextByRun: {},
      reasoningByRun: {},
      runStatuses: {},
    })
  })

  it('高频 delta 批量提交：33ms 内不更新，flush 后不丢字', () => {
    emitAgentEvent(textDelta('r1', 1, 'hello '))
    emitAgentEvent(textDelta('r1', 1, 'world'))
    // 未到 flush 窗口：state 暂不更新
    expect(useEventsStore.getState().streamTextByRun.r1?.[1]).toBeUndefined()
    vi.advanceTimersByTime(33)
    expect(useEventsStore.getState().streamTextByRun.r1?.[1]).toBe('hello world')
  })

  it('run / step 不串流', () => {
    emitAgentEvent(textDelta('r1', 1, 'a'))
    emitAgentEvent(textDelta('r2', 1, 'b'))
    emitAgentEvent(textDelta('r1', 2, 'c'))
    vi.advanceTimersByTime(33)
    expect(useEventsStore.getState().streamTextByRun.r1?.[1]).toBe('a')
    expect(useEventsStore.getState().streamTextByRun.r2?.[1]).toBe('b')
    expect(useEventsStore.getState().streamTextByRun.r1?.[2]).toBe('c')
  })

  it('结构化事件（如 model_completed）触发立即 flush', () => {
    emitAgentEvent(textDelta('r1', 1, 'partial'))
    // 不推进 timers，直接发结构化事件 → 立即 flush
    emitAgentEvent(structured('model_completed', 'r1'))
    expect(useEventsStore.getState().streamTextByRun.r1?.[1]).toBe('partial')
  })

  it('reasoning delta 独立累积，不与正文混在一起', () => {
    emitAgentEvent(textDelta('r1', 1, 'answer'))
    emitAgentEvent(reasoningDelta('r1', 1, '先分析'))
    vi.advanceTimersByTime(33)
    expect(useEventsStore.getState().streamTextByRun.r1?.[1]).toBe('answer')
    expect(useEventsStore.getState().reasoningByRun.r1?.[1]).toBe('先分析')
  })

  it('disconnect 时立即 flush 剩余增量', () => {
    emitAgentEvent(textDelta('r1', 1, 'tail'))
    useEventsStore.getState().disconnect()
    expect(useEventsStore.getState().streamTextByRun.r1?.[1]).toBe('tail')
  })
})
