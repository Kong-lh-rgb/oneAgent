/** 独立审批浮窗 RPC 生命周期测试。 */

import { describe, expect, it, vi } from 'vitest'

import type { ApprovalRequest } from '../api/types'
import {
  connectComputerApprovalStream,
  type ApprovalStreamClient,
} from './computerApprovalStream'

function approval(id: string, toolName = 'computer_click'): ApprovalRequest {
  return {
    id,
    run_id: 'run-1',
    conversation_id: 'conv-1',
    tool_name: toolName,
    tool_call_id: `call-${id}`,
    arguments: {},
    reason: '',
    status: 'pending',
    created_at: '2026-08-21T00:00:00+00:00',
    resolved_at: null,
  }
}

class FakeApprovalStreamClient implements ApprovalStreamClient {
  connected = false
  connect = vi.fn()
  disconnect = vi.fn()
  handlers = new Map<string, (params: unknown) => void>()
  statusListener: ((connected: boolean) => void) | null = null

  on(method: string, handler: (params: unknown) => void): () => void {
    this.handlers.set(method, handler)
    return () => this.handlers.delete(method)
  }

  setStatusListener(listener: (connected: boolean) => void): () => void {
    this.statusListener = listener
    return () => {
      this.statusListener = null
    }
  }
}

describe('connectComputerApprovalStream', () => {
  it('浮窗建立自己的连接，并在连接成功与重连后同步 pending', async () => {
    const client = new FakeApprovalStreamClient()
    const syncPending = vi.fn(async () => {})
    const stop = connectComputerApprovalStream(client, {
      syncPending,
      onRequired: vi.fn(),
      onResolved: vi.fn(),
      onAgentEvent: vi.fn(),
      onRunStatus: vi.fn(),
    })

    expect(client.connect).toHaveBeenCalledOnce()
    expect(syncPending).not.toHaveBeenCalled()

    client.statusListener?.(true)
    await Promise.resolve()
    expect(syncPending).toHaveBeenCalledTimes(1)

    client.statusListener?.(false)
    client.statusListener?.(true)
    await Promise.resolve()
    expect(syncPending).toHaveBeenCalledTimes(2)

    stop()
    expect(client.disconnect).toHaveBeenCalledOnce()
    expect(client.handlers.size).toBe(0)
    expect(client.statusListener).toBeNull()
  })

  it('转发 required/resolved，清理后不再接收事件', () => {
    const client = new FakeApprovalStreamClient()
    const onRequired = vi.fn()
    const onResolved = vi.fn()
    const onAgentEvent = vi.fn()
    const onRunStatus = vi.fn()
    const stop = connectComputerApprovalStream(client, {
      syncPending: async () => {},
      onRequired,
      onResolved,
      onAgentEvent,
      onRunStatus,
    })

    client.handlers.get('approval.required')?.({ approval: approval('a') })
    client.handlers.get('approval.resolved')?.({ approval: approval('a') })
    client.handlers.get('agent.event')?.({ event_id: 'event-1', run_id: 'run-1' })
    client.handlers.get('run.status')?.({ run_id: 'run-1', status: 'running' })
    expect(onRequired).toHaveBeenCalledWith(expect.objectContaining({ id: 'a' }))
    expect(onResolved).toHaveBeenCalledWith(expect.objectContaining({ id: 'a' }))
    expect(onAgentEvent).toHaveBeenCalledWith(
      expect.objectContaining({ event_id: 'event-1' }),
    )
    expect(onRunStatus).toHaveBeenCalledWith('run-1', 'running')

    stop()
    client.handlers.get('approval.required')?.({ approval: approval('b') })
    expect(onRequired).toHaveBeenCalledTimes(1)
  })

  it('连接已存在时立即同步 pending', async () => {
    const client = new FakeApprovalStreamClient()
    client.connected = true
    const syncPending = vi.fn(async () => {})
    connectComputerApprovalStream(client, {
      syncPending,
      onRequired: vi.fn(),
      onResolved: vi.fn(),
      onAgentEvent: vi.fn(),
      onRunStatus: vi.fn(),
    })

    await Promise.resolve()
    expect(syncPending).toHaveBeenCalledOnce()
  })
})
