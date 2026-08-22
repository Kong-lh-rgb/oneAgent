/** 扩展能力 JSON-RPC 客户端测试。 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const { callMock } = vi.hoisted(() => ({ callMock: vi.fn() }))

vi.mock('../rpc', () => ({ rpcClient: { call: callMock } }))

import {
  addMCPServer,
  deleteMCPServer,
  deleteSkill,
  installSkill,
  listExtensions,
  previewExtensionImport,
  applyExtensionImport,
  setMCPServerEnabled,
  setSkillEnabled,
} from './extensions'

describe('extensions api', () => {
  beforeEach(() => callMock.mockReset())

  it('读取扩展列表', async () => {
    callMock.mockResolvedValue({ skills: [], skill_diagnostics: [], mcp: { servers: [] } })
    await listExtensions()
    expect(callMock).toHaveBeenCalledWith('extension.list', {})
  })

  it('结构化安装 Skill', async () => {
    callMock.mockResolvedValue({ skill: { name: 'demo' } })
    await installSkill({
      name: 'demo',
      description: '说明',
      instructions: '步骤',
      scope: 'project',
    })
    expect(callMock).toHaveBeenCalledWith('skill.install', {
      name: 'demo',
      description: '说明',
      instructions: '步骤',
      scope: 'project',
    })
  })

  it('MCP 参数以数组和对象传输，不发送原始 JSON 字符串', async () => {
    callMock.mockResolvedValue({ server: { name: 'demo' }, restart_required: true })
    await addMCPServer({
      name: 'demo',
      command: 'npx',
      args: ['-y', 'demo-mcp'],
      env: { API_KEY: '${API_KEY}' },
      enabled: true,
      permission: 'human_approval',
    })
    expect(callMock).toHaveBeenCalledWith('mcp.add', {
      name: 'demo',
      command: 'npx',
      args: ['-y', 'demo-mcp'],
      env: { API_KEY: '${API_KEY}' },
      enabled: true,
      permission: 'human_approval',
    })
  })

  it('Skill 与 MCP 控制操作携带精确身份和状态', async () => {
    callMock.mockResolvedValue({ skill: { name: 'demo' }, server: { name: 'tools' } })
    await setSkillEnabled('demo', 'project', false)
    await deleteSkill('demo', 'project', false)
    await setMCPServerEnabled('tools', false)
    await deleteMCPServer('tools')
    expect(callMock).toHaveBeenNthCalledWith(1, 'skill.set_enabled', {
      name: 'demo', scope: 'project', enabled: false,
    })
    expect(callMock).toHaveBeenNthCalledWith(2, 'skill.delete', {
      name: 'demo', scope: 'project', enabled: false,
    })
    expect(callMock).toHaveBeenNthCalledWith(3, 'mcp.set_enabled', {
      name: 'tools', enabled: false,
    })
    expect(callMock).toHaveBeenNthCalledWith(4, 'mcp.delete', { name: 'tools' })
  })

  it('统一导入先预览，再携带指纹和明确确认执行', async () => {
    const input = {
      input: 'owner/repo',
      skill_scope: 'project' as const,
      mcp_permission: 'human_approval' as const,
    }
    callMock.mockResolvedValueOnce({ plan: { fingerprint: 'sha256', items: [] } })
    await previewExtensionImport(input)
    expect(callMock).toHaveBeenLastCalledWith('extension.import.preview', input)

    callMock.mockResolvedValueOnce({ skills: [], mcp_servers: [] })
    await applyExtensionImport({ ...input, fingerprint: 'sha256', confirmed: true })
    expect(callMock).toHaveBeenLastCalledWith('extension.import.apply', {
      ...input,
      fingerprint: 'sha256',
      confirmed: true,
    })
  })
})
