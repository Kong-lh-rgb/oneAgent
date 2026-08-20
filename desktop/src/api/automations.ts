/** Automation API：全部走共享 JSON-RPC WebSocket（结构化 schedule）。 */

import { rpcClient } from '../rpc'
import { RpcMethods } from '../rpc/methods'
import type { Automation, AutomationKind } from './types'

export interface CreateAutomationInput {
  title: string
  prompt: string
  kind: AutomationKind
  run_at?: string
  interval_seconds?: number
  cron_expr?: string
  timezone?: string
  conversation_id?: string
}

export async function listAutomations(): Promise<Automation[]> {
  const data = await rpcClient.call<{ automations: Automation[] }>(
    RpcMethods.automationList,
    {},
  )
  return data.automations
}

export async function getAutomation(id: string): Promise<Automation> {
  const data = await rpcClient.call<{ automation: Automation }>(
    RpcMethods.automationGet,
    { automation_id: id },
  )
  return data.automation
}

export async function createAutomation(
  input: CreateAutomationInput,
): Promise<Automation> {
  const data = await rpcClient.call<{ automation: Automation }>(
    RpcMethods.automationCreate,
    { ...input },
  )
  return data.automation
}

export async function pauseAutomation(id: string): Promise<Automation> {
  const data = await rpcClient.call<{ automation: Automation }>(
    RpcMethods.automationPause,
    { automation_id: id },
  )
  return data.automation
}

export async function resumeAutomation(id: string): Promise<Automation> {
  const data = await rpcClient.call<{ automation: Automation }>(
    RpcMethods.automationResume,
    { automation_id: id },
  )
  return data.automation
}

export async function cancelAutomation(id: string): Promise<Automation> {
  const data = await rpcClient.call<{ automation: Automation }>(
    RpcMethods.automationCancel,
    { automation_id: id },
  )
  return data.automation
}
