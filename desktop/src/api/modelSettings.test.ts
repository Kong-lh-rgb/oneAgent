/** 模型设置 JSON-RPC 客户端测试。 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const { callMock } = vi.hoisted(() => ({ callMock: vi.fn() }))

vi.mock('../rpc', () => ({ rpcClient: { call: callMock } }))

import { getModelSettings, testModelConnection, updateModelSettings } from './modelSettings'

const provider = {
  provider: 'qwen' as const,
  model: 'qwen-test',
  base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
  api_style: 'chat_completions' as const,
  api_key: 'temporary-key',
}

describe('model settings api', () => {
  beforeEach(() => callMock.mockReset())

  it('通过独立方法读取模型设置', async () => {
    callMock.mockResolvedValue({ providers: [] })
    await getModelSettings()
    expect(callMock).toHaveBeenCalledWith('model_settings.get', {})
  })

  it('保存完整设置快照', async () => {
    const input = {
      default_provider: 'qwen' as const,
      providers: [provider],
      reflection: { enabled: true, inherit_main: true, provider: null, model: null },
      maintenance: { enabled: false, inherit_main: true, provider: null, model: null },
    }
    callMock.mockResolvedValue({})
    await updateModelSettings(input)
    expect(callMock).toHaveBeenCalledWith('model_settings.update', input)
  })

  it('连接测试只发送当前 Provider 配置', async () => {
    callMock.mockResolvedValue({ success: true })
    await testModelConnection(provider)
    expect(callMock).toHaveBeenCalledWith('model_settings.test', provider)
  })
})
