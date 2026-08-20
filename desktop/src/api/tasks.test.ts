/** Plan Mode Desktop API 测试：conversation.send 带 mode / task get·accept·reject。 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const { callMock } = vi.hoisted(() => ({ callMock: vi.fn() }))

vi.mock('../rpc', () => ({
  rpcClient: { call: callMock },
}))

import { sendMessage } from './conversations'
import { getTask, planAccept, planReject } from './tasks'

describe('plan mode desktop api', () => {
  beforeEach(() => {
    callMock.mockReset()
  })

  it('conversation.send 默认 normal mode', async () => {
    callMock.mockResolvedValue({ run: { id: 'run-1' } })
    await sendMessage('conv-1', 'hello')
    expect(callMock).toHaveBeenCalledWith(
      'conversation.send',
      {
        conversation_id: 'conv-1',
        content: 'hello',
        mode: 'normal',
      },
      { timeoutMs: 0 },
    )
  })

  it('conversation.send 显式 plan mode', async () => {
    callMock.mockResolvedValue({
      plan_task_id: 'task-1',
      run: { id: 'run-2', mode: 'plan' },
    })
    const data = await sendMessage('conv-1', '帮我实现 Computer Runtime', 'plan')
    expect(callMock).toHaveBeenCalledWith(
      'conversation.send',
      {
        conversation_id: 'conv-1',
        content: '帮我实现 Computer Runtime',
        mode: 'plan',
      },
      { timeoutMs: 0 },
    )
    expect(data.plan_task_id).toBe('task-1')
  })

  it('task.get 拉取计划详情', async () => {
    callMock.mockResolvedValue({ task: { id: 'task-1', status: 'pending' } })
    const task = await getTask('task-1')
    expect(callMock).toHaveBeenCalledWith('task.get', { task_id: 'task-1' })
    expect(task.status).toBe('pending')
  })

  it('task.plan_accept 调用 accept RPC', async () => {
    callMock.mockResolvedValue({ task: { id: 'task-1', status: 'active' } })
    const task = await planAccept('task-1')
    expect(callMock).toHaveBeenCalledWith('task.plan_accept', { task_id: 'task-1' })
    expect(task.status).toBe('active')
  })

  it('task.plan_reject 调用 reject RPC', async () => {
    callMock.mockResolvedValue({ task: { id: 'task-1', status: 'cancelled' } })
    const task = await planReject('task-1')
    expect(callMock).toHaveBeenCalledWith('task.plan_reject', { task_id: 'task-1' })
    expect(task.status).toBe('cancelled')
  })
})
