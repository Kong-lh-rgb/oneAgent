/** 长期记忆只读 RPC 客户端测试。 */

import { describe, expect, it, vi } from 'vitest'

const { callMock } = vi.hoisted(() => ({ callMock: vi.fn() }))

vi.mock('../rpc', () => ({ rpcClient: { call: callMock } }))

import { listMemories } from './memories'

describe('memory api', () => {
  it('通过 memory.list 读取长期记忆', async () => {
    callMock.mockResolvedValue({ core: '', active: [], archived: [] })
    await listMemories()
    expect(callMock).toHaveBeenCalledWith('memory.list', {})
  })
})
