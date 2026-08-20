/** 共享 JSON-RPC 2.0 WebSocket 客户端。

Renderer 全局只应有一个实例（一条连接）。负责：
- request/response correlation（pending request map + id 自增）；
- notification 分发（``on`` / ``off``）；
- 断线后 reject 当前 pending，简单自动重连（不自动重发 mutation）；
- 注入式 ``socketFactory`` 便于单元测试。
*/

import { RpcError, RpcErrorCode } from './errors'
import { encodeRequest, parseMessage } from './protocol'

export type NotificationHandler = (params: unknown) => void
export type StatusListener = (connected: boolean) => void

export interface RpcClientOptions {
  url: string
  reconnectDelayMs?: number
  requestTimeoutMs?: number
  socketFactory?: () => WebSocket
}

interface PendingRequest {
  resolve: (value: unknown) => void
  reject: (error: RpcError) => void
  timer: ReturnType<typeof setTimeout>
}

interface OpenWaiter {
  resolve: () => void
  reject: (error: RpcError) => void
  timer: ReturnType<typeof setTimeout>
}

const WS_CONNECTING = 0
const WS_OPEN = 1

export class RpcClient {
  private socket: WebSocket | null = null
  private nextId = 1
  private pending = new Map<number, PendingRequest>()
  private handlers = new Map<string, Set<NotificationHandler>>()
  private statusListeners = new Set<StatusListener>()
  private openWaiters: OpenWaiter[] = []
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private shouldReconnect = false
  private connectPromise: Promise<void> | null = null

  private readonly url: string
  private readonly reconnectDelayMs: number
  private readonly requestTimeoutMs: number
  private readonly openTimeoutMs: number
  private readonly socketFactory: () => WebSocket

  constructor(options: RpcClientOptions) {
    this.url = options.url
    this.reconnectDelayMs = options.reconnectDelayMs ?? 2000
    this.requestTimeoutMs = options.requestTimeoutMs ?? 60_000
    this.openTimeoutMs = 8000
    this.socketFactory = options.socketFactory ?? (() => new WebSocket(this.url))
  }

  get connected(): boolean {
    return this.socket !== null && this.socket.readyState === WS_OPEN
  }

  // ------------------------------------------------------------------
  // 生命周期
  // ------------------------------------------------------------------

  connect(): void {
    this.shouldReconnect = true
    if (this.socket && this.socket.readyState !== 3) {
      return // 已在连接中或已连接
    }
    if (this.connectPromise) return
    this.connectPromise = this.openSocket()
  }

  disconnect(): void {
    this.shouldReconnect = false
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    const socket = this.socket
    this.socket = null
    if (socket) {
      socket.onclose = null
      socket.onmessage = null
      socket.close()
    }
    this.rejectAllPending(
      new RpcError(RpcErrorCode.NotConnected, 'client disconnected'),
    )
    this.rejectOpenWaiters(
      new RpcError(RpcErrorCode.NotConnected, 'client disconnected'),
    )
    this.emitStatus(false)
  }

  private async openSocket(): Promise<void> {
    const socket = this.socketFactory()
    this.socket = socket
    // 创建时同步挂上 message/close 处理器，避免 open 后消息竞态。
    socket.onmessage = (event: MessageEvent<string>) => this.handleMessage(event.data)
    socket.onclose = () => this.handleClose(socket)
    try {
      await new Promise<void>((resolve, reject) => {
        socket.onopen = () => resolve()
        socket.onerror = () =>
          reject(new RpcError(RpcErrorCode.NotConnected, 'websocket error'))
      })
      this.flushOpenWaiters()
      this.emitStatus(true)
    } catch {
      this.handleClose(socket)
    } finally {
      this.connectPromise = null
    }
  }

  private handleClose(socket: WebSocket): void {
    if (this.socket !== socket) return // 过期 socket（重连后旧实例）
    this.socket = null
    this.rejectAllPending(
      new RpcError(RpcErrorCode.NotConnected, 'connection closed'),
    )
    this.rejectOpenWaiters(
      new RpcError(RpcErrorCode.NotConnected, 'connection closed'),
    )
    this.emitStatus(false)
    if (this.shouldReconnect && this.reconnectTimer === null) {
      // 1~3 秒自动重连；不重发任何 mutation。
      const delay = this.reconnectDelayMs + Math.floor(Math.random() * 1000)
      this.reconnectTimer = setTimeout(() => {
        this.reconnectTimer = null
        this.connect()
      }, delay)
    }
  }

  // ------------------------------------------------------------------
  // 请求 / 响应
  // ------------------------------------------------------------------

  async call<T>(method: string, params?: Record<string, unknown>): Promise<T> {
    if (!this.socket) {
      throw new RpcError(RpcErrorCode.NotConnected, 'not connected')
    }
    if (this.socket.readyState !== WS_OPEN) {
      await this.waitForOpen()
    }
    return this.sendRequest<T>(method, params)
  }

  private waitForOpen(): Promise<void> {
    return new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.openWaiters = this.openWaiters.filter((waiter) => waiter.timer !== timer)
        reject(
          new RpcError(RpcErrorCode.NotConnected, 'connection open timeout'),
        )
      }, this.openTimeoutMs)
      this.openWaiters.push({
        resolve: () => {
          clearTimeout(timer)
          resolve()
        },
        reject: (error) => {
          clearTimeout(timer)
          reject(error)
        },
        timer,
      })
    })
  }

  private sendRequest<T>(method: string, params?: Record<string, unknown>): Promise<T> {
    const id = this.nextId++
    const socket = this.socket
    if (!socket) {
      return Promise.reject(new RpcError(RpcErrorCode.NotConnected, 'not connected'))
    }
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id)
        reject(new RpcError(RpcErrorCode.RequestTimeout, `request timeout: ${method}`))
      }, this.requestTimeoutMs)
      this.pending.set(id, {
        resolve: resolve as (value: unknown) => void,
        reject,
        timer,
      })
      socket.send(encodeRequest(id, method, params))
    })
  }

  private handleMessage(text: string): void {
    let message: {
      id?: number
      method?: string
      result?: unknown
      error?: { code: number; message: string; data?: unknown }
    }
    try {
      message = parseMessage(text)
    } catch {
      return // 忽略无法解析的消息
    }

    if (message.id !== undefined) {
      const pending = this.pending.get(message.id)
      if (!pending) return
      clearTimeout(pending.timer)
      this.pending.delete(message.id)
      if (message.error !== undefined) {
        pending.reject(
          new RpcError(message.error.code, message.error.message, message.error.data),
        )
      } else {
        pending.resolve(message.result)
      }
      return
    }

    if (message.method !== undefined) {
      const handlers = this.handlers.get(message.method)
      if (handlers) {
        for (const handler of [...handlers]) {
          try {
            handler(message.params)
          } catch {
            // 单个 handler 异常不影响其它 handler
          }
        }
      }
    }
  }

  // ------------------------------------------------------------------
  // 通知订阅
  // ------------------------------------------------------------------

  on(method: string, handler: NotificationHandler): () => void {
    let set = this.handlers.get(method)
    if (!set) {
      set = new Set()
      this.handlers.set(method, set)
    }
    set.add(handler)
    return () => this.off(method, handler)
  }

  off(method: string, handler: NotificationHandler): void {
    const set = this.handlers.get(method)
    if (!set) return
    set.delete(handler)
    if (set.size === 0) this.handlers.delete(method)
  }

  setStatusListener(listener: StatusListener): () => void {
    this.statusListeners.add(listener)
    return () => this.statusListeners.delete(listener)
  }

  // ------------------------------------------------------------------
  // 内部
  // ------------------------------------------------------------------

  private flushOpenWaiters(): void {
    const waiters = this.openWaiters
    this.openWaiters = []
    for (const waiter of waiters) {
      clearTimeout(waiter.timer)
      waiter.resolve()
    }
  }

  private rejectOpenWaiters(error: RpcError): void {
    const waiters = this.openWaiters
    this.openWaiters = []
    for (const waiter of waiters) {
      clearTimeout(waiter.timer)
      waiter.reject(error)
    }
  }

  private rejectAllPending(error: RpcError): void {
    const entries = [...this.pending.values()]
    this.pending.clear()
    for (const entry of entries) {
      clearTimeout(entry.timer)
      entry.reject(error)
    }
  }

  private emitStatus(connected: boolean): void {
    for (const listener of this.statusListeners) {
      try {
        listener(connected)
      } catch {
        // 忽略
      }
    }
  }
}
