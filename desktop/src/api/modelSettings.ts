/** 模型设置：非敏感配置由 Host 落盘，API Key 由 macOS Keychain 保存。 */

import { rpcClient } from '../rpc'
import { RpcMethods } from '../rpc/methods'

export type ModelProvider = 'openai' | 'qwen' | 'deepseek' | 'anthropic'
export type ApiStyle = 'responses' | 'chat_completions' | 'anthropic_messages'

export interface ProviderModelSettings {
  provider: ModelProvider
  label: string
  model: string
  base_url: string | null
  api_style: ApiStyle
  configured: boolean
  key_source: 'keychain' | 'environment' | 'none'
}

export interface ProviderModelSettingsUpdate {
  provider: ModelProvider
  model: string
  base_url: string | null
  api_style: ApiStyle
  api_key?: string | null
}

export interface ModelRoleSettings {
  enabled: boolean
  inherit_main: boolean
  provider: ModelProvider | null
  model: string | null
}

export interface ModelSettingsView {
  default_provider: ModelProvider
  providers: ProviderModelSettings[]
  reflection: ModelRoleSettings
  maintenance: ModelRoleSettings
  active_provider: string
  active_model: string
  restart_required: boolean
}

export interface ModelSettingsUpdate {
  default_provider: ModelProvider
  providers: ProviderModelSettingsUpdate[]
  reflection: ModelRoleSettings
  maintenance: ModelRoleSettings
}

export interface ModelConnectionResult {
  success: boolean
  provider: string
  model: string
  duration_ms: number
}

export function getModelSettings(): Promise<ModelSettingsView> {
  return rpcClient.call(RpcMethods.modelSettingsGet, {})
}

export function updateModelSettings(input: ModelSettingsUpdate): Promise<ModelSettingsView> {
  return rpcClient.call(RpcMethods.modelSettingsUpdate, { ...input })
}

export function testModelConnection(
  input: ProviderModelSettingsUpdate,
): Promise<ModelConnectionResult> {
  return rpcClient.call(RpcMethods.modelSettingsTest, { ...input })
}
