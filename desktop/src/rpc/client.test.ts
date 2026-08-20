/** RpcClient 单元测试：request/response correlation / notification / 重连。 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { RpcClient } from './client'
import { RpcError, RpcErrorCode } from './errors'

interface SentMessage {
  jsonrpc: string
  id?: number
  method?: string
  params?: unknown
}

class FakeWebSocket {
  readyState = 0 // CONNECTING
  sent: SentMessage[] = []
  closed = false
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null

  open(): void {
    this.readyState = 1
    this.onopen?.()
  }

  message(text: string): void {
    this.onmessage?.({ data: text })
  }

  close(): void {
    this.readyState = 3
    this.closed = true
    this.onclose?.()
  }

  error(): void {
    this.onerror?.()
  }

  send(text: string): void {
    this.sent.push(JSON.parse(text) as SentMessage)
  }
}

function makeClient(
  requestTimeoutMs = 60_000,
): { client: RpcClient; fakes: FakeWebSocket[]; factory: () => FakeWebSocket } {
  const fakes: FakeWebSocket[] = []
  const factory = (): FakeWebSocket => {
    const fake = new FakeWebSocket()
    fakes.push(fake)
    return fake
  }
  const client = new RpcClient({
    url: 'ws://127.0.0.1:8000/rpc',
    reconnectDelayMs: 2000,
    requestTimeoutMs,
    // FakeWebSocket 只实现客户端使用的最小结构，注入时做类型转换。
    socketFactory: factory as unknown as () => WebSocket,
  })
  return { client, fakes, factory }
}

function response(id: number, result: unknown): string {
  return JSON.stringify({ jsonrpc: '2.0', id, result })
}

function errorResponse(id: number, code: number, message: string): string {
  return JSON.stringify({ jsonrpc: '2.0', id, error: { code, message } })
}

function notification(method: string, params: unknown): string {
  return JSON.stringify({ jsonrpc: '2.0', method, params })
}

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('RpcClient', () => {
  it('自增 request id 并编码请求', async () => {
    const { client, fakes } = makeClient()
    client.connect()
    fakes[0].open()
    const p1 = client.call('conversation.list', { limit: 5 })
    const p2 = client.call('system.info')
    expect(fakes[0].sent).toHaveLength(2)
    expect(fakes[0].sent[0].id).toBe(1)
    expect(fakes[0].sent[0].method).toBe('conversation.list')
    expect(fakes[0].sent[0].params).toEqual({ limit: 5 })
    expect(fakes[0].sent[1].id).toBe(2)
    expect(fakes[0].sent[1].method).toBe('system.info')
    fakes[0].message(response(1, { ok: true }))
    fakes[0].message(response(2, { ok: true }))
    await p1
    await p2
  })

  it('response 按 id resolve 对应 Promise', async () => {
    const { client, fakes } = makeClient()
    client.connect()
    fakes[0].open()
    const promise = client.call<{ value: number }>('run.get', { run_id: 'abc' })
    fakes[0].message(response(1, { value: 42 }))
    await expect(promise).resolves.toEqual({ value: 42 })
  })

  it('error 按 id reject 对应 Promise', async () => {
    const { client, fakes } = makeClient()
    client.connect()
    fakes[0].open()
    const promise = client.call('run.cancel', { run_id: 'abc' })
    fakes[0].message(errorResponse(1, -32001, 'cannot cancel'))
    await expect(promise).rejects.toMatchObject({
      name: 'RpcError',
      code: -32001,
      message: 'cannot cancel',
    })
  })

  it('notification 触发对应 handler（不匹配 pending）', async () => {
    const { client, fakes } = makeClient()
    client.connect()
    fakes[0].open()
    const events: unknown[] = []
    const unsubscribe = client.on('agent.event', (params) => events.push(params))
    fakes[0].message(notification('agent.event', { run_id: 'r1', type: 'agent_started' }))
    fakes[0].message(notification('run.status', { run_id: 'r1', status: 'running' }))
    expect(events).toEqual([{ run_id: 'r1', type: 'agent_started' }])
    unsubscribe()
    fakes[0].message(notification('agent.event', { run_id: 'r1', type: 'agent_completed' }))
    expect(events).toHaveLength(1)
  })

  it('并发 pending request 正确 correlation（乱序响应）', async () => {
    const { client, fakes } = makeClient()
    client.connect()
    fakes[0].open()
    const p1 = client.call('conversation.get', { conversation_id: 'a' })
    const p2 = client.call('conversation.get', { conversation_id: 'b' })
    const p3 = client.call('conversation.get', { conversation_id: 'c' })
    // 乱序返回：3 → 1 → 2
    fakes[0].message(response(3, { id: 'c' }))
    fakes[0].message(response(1, { id: 'a' }))
    fakes[0].message(response(2, { id: 'b' }))
    await expect(p1).resolves.toEqual({ id: 'a' })
    await expect(p2).resolves.toEqual({ id: 'b' })
    await expect(p3).resolves.toEqual({ id: 'c' })
  })

  it('socket close 后 pending 全部 reject', async () => {
    const { client, fakes } = makeClient()
    client.connect()
    fakes[0].open()
    const p1 = client.call('conversation.send', { conversation_id: 'a' })
    const p2 = client.call('conversation.send', { conversation_id: 'b' })
    fakes[0].close()
    await expect(p1).rejects.toBeInstanceOf(RpcError)
    await expect(p2).rejects.toBeInstanceOf(RpcError)
  })

  it('reconnect 不自动重发 mutation', async () => {
    const { client, fakes } = makeClient()
    client.connect()
    fakes[0].open()
    const p1 = client.call('run.cancel', { run_id: 'abc' })
    // 断线：pending reject，并安排重连。
    fakes[0].close()
    await expect(p1).rejects.toBeInstanceOf(RpcError)
    expect(fakes[0].sent).toHaveLength(1)

    // 推进重连定时器 → 创建第二个 socket。
    await vi.advanceTimersByTimeAsync(3000)
    expect(fakes).toHaveLength(2)
    fakes[1].open()
    expect(client.connected).toBe(true)
    // 新 socket 不应自动重发旧的 mutation。
    expect(fakes[1].sent).toHaveLength(0)

    // 新 socket 上新的请求正常工作。
    const p2 = client.call('system.info')
    fakes[1].message(response(2, { ok: true }))
    await expect(p2).resolves.toEqual({ ok: true })
  })

  it('notification 与 response 交错到达', async () => {
    const { client, fakes } = makeClient()
    client.connect()
    fakes[0].open()
    const events: unknown[] = []
    client.on('agent.event', (params) => events.push(params))
    const promise = client.call('conversation.send', { conversation_id: 'a' })
    // 执行过程中先来 notification，再来 response。
    fakes[0].message(notification('agent.event', { run_id: 'r1', type: 'model_started' }))
    fakes[0].message(response(1, { content: '完成' }))
    fakes[0].message(notification('agent.event', { run_id: 'r1', type: 'agent_completed' }))
    await expect(promise).resolves.toEqual({ content: '完成' })
    expect(events).toHaveLength(2)
  })

  it('request timeout 会 reject', async () => {
    const { client, fakes } = makeClient(100)
    client.connect()
    fakes[0].open()
    const promise = client.call('run.list')
    // 先挂 rejection 断言，再推进定时器，避免未处理 rejection 告警。
    const assertion = expect(promise).rejects.toMatchObject({
      code: RpcErrorCode.RequestTimeout,
    })
    await vi.advanceTimersByTimeAsync(150)
    await assertion
  })
})
