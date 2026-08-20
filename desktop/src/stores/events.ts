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
  runStatuses: Record<string, string>
  connect: () => void
  disconnect: () => void
}

const MAX_EVENTS_PER_RUN = 500

export const useEventsStore = create<EventsState>((set, get) => {
  let unsubscribeStatus: (() => void) | null = null
  const unsubscribeHandlers: Array<() => void> = []

  const handleAgentEvent = (params: unknown): void => {
    const agentEvent = params as AgentEvent
    const runId = agentEvent.run_id
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
