/** Automation API 客户端（结构化 schedule，不做自然语言解析）。 */

import { apiGet, apiPost } from './http'
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
  const data = await apiGet<{ automations: Automation[] }>('/api/automations')
  return data.automations
}

export async function getAutomation(id: string): Promise<Automation> {
  const data = await apiGet<{ automation: Automation }>(`/api/automations/${id}`)
  return data.automation
}

export async function createAutomation(
  input: CreateAutomationInput,
): Promise<Automation> {
  const data = await apiPost<{ automation: Automation }>('/api/automations', input)
  return data.automation
}

export async function pauseAutomation(id: string): Promise<Automation> {
  const data = await apiPost<{ automation: Automation }>(
    `/api/automations/${id}/pause`,
  )
  return data.automation
}

export async function resumeAutomation(id: string): Promise<Automation> {
  const data = await apiPost<{ automation: Automation }>(
    `/api/automations/${id}/resume`,
  )
  return data.automation
}

export async function cancelAutomation(id: string): Promise<Automation> {
  const data = await apiPost<{ automation: Automation }>(
    `/api/automations/${id}/cancel`,
  )
  return data.automation
}
