/** 实时事件 Zustand store：WebSocket /api/events → agent_event / run_status。 */

import { create } from 'zustand'

import { WS_URL } from '../api/config'
import type { AgentEvent } from '../api/types'

interface EventsState {
  connected: boolean
  eventsByRun: Record<string, AgentEvent[]>
  runStatuses: Record<string, string>
  connect: () => void
  disconnect: () => void
}

const MAX_EVENTS_PER_RUN = 500

export const useEventsStore = create<EventsState>((set, get) => {
  let socket: WebSocket | null = null
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null

  function connect(): void {
    if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
      return
    }
    socket = new WebSocket(`${WS_URL}/api/events`)

    socket.onopen = () => {
      set({ connected: true })
    }
    socket.onmessage = (event: MessageEvent<string>) => {
      let message: { type: string; data: unknown }
      try {
        message = JSON.parse(event.data) as { type: string; data: unknown }
      } catch {
        return
      }
      if (message.type === 'agent_event') {
        const agentEvent = message.data as AgentEvent
        const runId = agentEvent.run_id
        const existing = get().eventsByRun[runId] ?? []
        if (existing.some((item) => item.event_id === agentEvent.event_id)) {
          return
        }
        const next = [...existing, agentEvent].slice(-MAX_EVENTS_PER_RUN)
        set({
          eventsByRun: { ...get().eventsByRun, [runId]: next },
        })
      } else if (message.type === 'run_status') {
        const data = message.data as { run_id: string; status: string }
        set({
          runStatuses: { ...get().runStatuses, [data.run_id]: data.status },
        })
      }
    }
    socket.onclose = () => {
      set({ connected: false })
      // 自动重连（本地 Agent Server 可能在 Desktop 之后启动）。
      if (reconnectTimer === null) {
        reconnectTimer = setTimeout(() => {
          reconnectTimer = null
          connect()
        }, 3000)
      }
    }
    socket.onerror = () => {
      socket?.close()
    }
  }

  function disconnect(): void {
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    if (socket) {
      socket.onclose = null
      socket.close()
      socket = null
    }
    set({ connected: false })
  }

  return {
    connected: false,
    eventsByRun: {},
    runStatuses: {},
    connect,
    disconnect,
  }
})
