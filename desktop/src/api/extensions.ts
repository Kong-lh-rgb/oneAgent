/** 扩展能力 API：结构化安装 Skill，并由 Host 生成安全的 MCP JSON。 */

import { rpcClient } from '../rpc'
import { RpcMethods } from '../rpc/methods'

export interface InstalledSkill {
  name: string
  description: string
  scope: 'user' | 'project'
  location: string
  enabled: boolean
}

export interface SkillDiagnostic {
  name: string
  scope: 'user' | 'project'
  location: string
  reason: string
}

export type MCPPermission = 'allowed' | 'human_approval' | 'forbidden'
export type MCPServerState =
  | 'stopped'
  | 'starting'
  | 'running'
  | 'failed'
  | 'restart_required'

export interface ManagedMCPServer {
  name: string
  command: string
  args: string[]
  cwd: string | null
  enabled: boolean
  permission: MCPPermission
  env_names: string[]
  state: MCPServerState
  tool_names: string[]
  error: string | null
}

export interface ExtensionsOverview {
  skills: InstalledSkill[]
  skill_diagnostics: SkillDiagnostic[]
  mcp: {
    config_path: string
    error: string | null
    restart_required: boolean
    servers: ManagedMCPServer[]
  }
}

export interface InstallSkillInput {
  name: string
  description: string
  instructions: string
  scope: 'user' | 'project'
}

export interface AddMCPServerInput {
  name: string
  command: string
  args: string[]
  env: Record<string, string>
  cwd?: string
  enabled: boolean
  permission: MCPPermission
  startup_timeout_seconds?: number
  call_timeout_seconds?: number
}

export interface ExtensionImportInput {
  input: string
  skill_scope: 'user' | 'project'
  mcp_permission: MCPPermission
}

export interface ExtensionImportPlanItem {
  kind: 'skill' | 'mcp'
  name: string
  source: string
  summary: string
  scope?: 'user' | 'project'
  command?: string
  args?: string[]
  cwd?: string | null
  env_names?: string[]
  permission?: MCPPermission
}

export interface ExtensionImportPlan {
  fingerprint: string
  items: ExtensionImportPlanItem[]
  actions: string[]
  warnings: string[]
  requires_download: boolean
  requires_restart: boolean
}

export interface ExtensionImportResult {
  skills: Array<{ name: string; scope: 'user' | 'project'; source: string }>
  mcp_servers: string[]
  restart_required: boolean
}

export async function listExtensions(): Promise<ExtensionsOverview> {
  return rpcClient.call<ExtensionsOverview>(RpcMethods.extensionList, {})
}

export async function previewExtensionImport(
  input: ExtensionImportInput,
): Promise<ExtensionImportPlan> {
  const result = await rpcClient.call<{ plan: ExtensionImportPlan }>(
    RpcMethods.extensionImportPreview,
    { ...input },
  )
  return result.plan
}

export async function applyExtensionImport(
  input: ExtensionImportInput & { fingerprint: string; confirmed: true },
): Promise<ExtensionImportResult> {
  return rpcClient.call<ExtensionImportResult>(
    RpcMethods.extensionImportApply,
    { ...input },
  )
}

export async function installSkill(input: InstallSkillInput): Promise<InstalledSkill> {
  const result = await rpcClient.call<{ skill: InstalledSkill }>(
    RpcMethods.skillInstall,
    { ...input },
  )
  return result.skill
}

export async function setSkillEnabled(
  name: string,
  scope: InstalledSkill['scope'],
  enabled: boolean,
): Promise<InstalledSkill> {
  const result = await rpcClient.call<{ skill: InstalledSkill }>(
    RpcMethods.skillSetEnabled,
    { name, scope, enabled },
  )
  return result.skill
}

export async function deleteSkill(
  name: string,
  scope: InstalledSkill['scope'],
  enabled: boolean,
): Promise<void> {
  await rpcClient.call(RpcMethods.skillDelete, { name, scope, enabled })
}

export async function addMCPServer(
  input: AddMCPServerInput,
): Promise<{ server: ManagedMCPServer; restart_required: boolean; config_path: string }> {
  return rpcClient.call<{
    server: ManagedMCPServer
    restart_required: boolean
    config_path: string
  }>(RpcMethods.mcpAdd, { ...input })
}

export async function setMCPServerEnabled(
  name: string,
  enabled: boolean,
): Promise<ManagedMCPServer> {
  const result = await rpcClient.call<{ server: ManagedMCPServer }>(
    RpcMethods.mcpSetEnabled,
    { name, enabled },
  )
  return result.server
}

export async function deleteMCPServer(name: string): Promise<void> {
  await rpcClient.call(RpcMethods.mcpDelete, { name })
}
