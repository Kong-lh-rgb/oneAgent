/** 实时事件 Zustand store：订阅共享 RpcClient 的 agent.event / run.status。

不再自己 new WebSocket —— 全 Renderer 只有一个共享 RpcClient（src/rpc）。
WebSocket notification 只用于实时 UI 更新；SQLite 才是 durable source of truth，
断线重连后由页面通过 query/refetch 恢复。
*/

import { create } from 'zustand'

import type { AgentEvent } from '../api/types'
import { rpcClient } from '../rpc'

interface EventsState {
  connected: boolean
  eventsByRun: Record<string, AgentEvent[]>
  streamTextByRun: Record<string, Record<number, string>>
  /** 流式思考增量（model_reasoning_delta）按 run+step 累积。 */
  reasoningByRun: Record<string, Record<number, string>>
  runStatuses: Record<string, string>
  connect: () => void
  disconnect: () => void
}

const MAX_EVENTS_PER_RUN = 500

// ---------------------------------------------------------------------------
// 流式增量批量提交：model_output_delta / model_reasoning_delta 是高频事件
// （每个 token 一个），不直接触发 Zustand set（避免整个 Renderer 每 token
// 全量重渲染）。先把增量攒进 pending buffer，~33ms flush 一次，批量合并。
// 收到结构化事件 / 断开时立即 flush，保证 complete 时文本不丢失。
// ---------------------------------------------------------------------------

const STREAM_FLUSH_MS = 33

interface PendingStreamDelta {
  runId: string
  step: number
  delta: string
}

let pendingText: PendingStreamDelta[] = []
let pendingReasoning: PendingStreamDelta[] = []
let flushTimer: ReturnType<typeof setTimeout> | null = null

type StoreApi = EventsState

function flushPending(api: {
  getState: () => StoreApi
  setState: (partial: Partial<EventsState>) => void
}): void {
  if (flushTimer !== null) {
    clearTimeout(flushTimer)
    flushTimer = null
  }
  if (pendingText.length === 0 && pendingReasoning.length === 0) return

  const state = api.getState()
  const streamTextByRun: EventsState['streamTextByRun'] = { ...state.streamTextByRun }
  const reasoningByRun: EventsState['reasoningByRun'] = { ...state.reasoningByRun }

  for (const item of pendingText) {
    const runText = { ...(streamTextByRun[item.runId] ?? {}) }
    runText[item.step] = `${runText[item.step] ?? ''}${item.delta}`
    streamTextByRun[item.runId] = runText
  }
  for (const item of pendingReasoning) {
    const runReasoning = { ...(reasoningByRun[item.runId] ?? {}) }
    runReasoning[item.step] = `${runReasoning[item.step] ?? ''}${item.delta}`
    reasoningByRun[item.runId] = runReasoning
  }

  pendingText = []
  pendingReasoning = []
  api.setState({ streamTextByRun, reasoningByRun })
}

function scheduleFlush(api: {
  getState: () => StoreApi
  setState: (partial: Partial<EventsState>) => void
}): void {
  if (flushTimer !== null) return
  flushTimer = setTimeout(() => flushPending(api), STREAM_FLUSH_MS)
}

export const useEventsStore = create<EventsState>((set, get) => {
  let unsubscribeStatus: (() => void) | null = null
  const unsubscribeHandlers: Array<() => void> = []

  // 供 flushPending 使用的轻量 api 适配。
  const flushApi = {
    getState: () => get(),
    setState: (partial: Partial<EventsState>) => set(partial),
  }

  const flushNow = (): void => flushPending(flushApi)

  const handleAgentEvent = (params: unknown): void => {
    const agentEvent = params as AgentEvent
    const runId = agentEvent.run_id
    if (
      agentEvent.type === 'model_output_delta' &&
      agentEvent.step !== null &&
      agentEvent.delta
    ) {
      pendingText.push({ runId, step: agentEvent.step, delta: agentEvent.delta })
      scheduleFlush(flushApi)
      return
    }
    if (
      agentEvent.type === 'model_reasoning_delta' &&
      agentEvent.step !== null &&
      agentEvent.reasoning_delta
    ) {
      pendingReasoning.push({
        runId,
        step: agentEvent.step,
        delta: agentEvent.reasoning_delta,
      })
      scheduleFlush(flushApi)
      return
    }

    // 结构化事件：先把流式缓冲落盘，避免漏字。
    flushNow()

    const existing = get().eventsByRun[runId] ?? []
    if (existing.some((item) => item.event_id === agentEvent.event_id)) {
      return
    }
    const next = [...existing, agentEvent].slice(-MAX_EVENTS_PER_RUN)
    set({ eventsByRun: { ...get().eventsByRun, [runId]: next } })
  }

  const handleRunStatus = (params: unknown): void => {
    const data = params as { run_id: string; status: string }
    set({ runStatuses: { ...get().runStatuses, [data.run_id]: data.status } })
  }

  return {
    connected: false,
    eventsByRun: {},
    streamTextByRun: {},
    reasoningByRun: {},
    runStatuses: {},
    connect: () => {
      if (unsubscribeStatus) return // 只订阅一次
      unsubscribeStatus = rpcClient.setStatusListener((connected) =>
        set({ connected }),
      )
      unsubscribeHandlers.push(rpcClient.on('agent.event', handleAgentEvent))
      unsubscribeHandlers.push(rpcClient.on('run.status', handleRunStatus))
      rpcClient.connect()
    },
    disconnect: () => {
      flushNow()
      unsubscribeStatus?.()
      unsubscribeStatus = null
      while (unsubscribeHandlers.length > 0) {
        unsubscribeHandlers.pop()?.()
      }
      rpcClient.disconnect()
      set({ connected: false })
    },
  }
})
