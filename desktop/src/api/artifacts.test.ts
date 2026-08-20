import { beforeEach, describe, expect, it, vi } from 'vitest'

const { callMock } = vi.hoisted(() => ({ callMock: vi.fn() }))

vi.mock('../rpc', () => ({ rpcClient: { call: callMock } }))

import {
  buildArtifactDownloadUrl,
  getArtifact,
  listArtifacts,
} from './artifacts'
import { SERVER_URL } from './config'

describe('artifact api', () => {
  beforeEach(() => callMock.mockReset())

  it('artifact.list 映射筛选参数', async () => {
    callMock.mockResolvedValue({ artifacts: [] })
    await listArtifacts({ runId: 'run-1', conversationId: 'conv-1', limit: 12 })
    expect(callMock).toHaveBeenCalledWith('artifact.list', {
      run_id: 'run-1',
      conversation_id: 'conv-1',
      limit: 12,
    })
  })

  it('artifact.get 使用 opaque id', async () => {
    callMock.mockResolvedValue({ artifact: { id: 'abc' } })
    await getArtifact('abc')
    expect(callMock).toHaveBeenCalledWith('artifact.get', { id: 'abc' })
  })

  it('下载 URL 只使用 artifact id', () => {
    const id = 'a'.repeat(32)
    const url = buildArtifactDownloadUrl(id)
    expect(url).toBe(`${SERVER_URL}/artifacts/${id}/content`)
    expect(url).not.toContain('/Users/')
    expect(url).not.toContain('.vesta')
    expect(url).not.toContain('file://')
  })
})
