/** Run API 客户端。 */

import { apiGet, apiPost } from './http'
import type { AgentEvent, AgentRunTrace, AgentResult, Run } from './types'

export interface RunListQuery {
  conversationId?: string
  status?: string
  limit?: number
}

export async function listRuns(query: RunListQuery = {}): Promise<Run[]> {
  const params = new URLSearchParams()
  if (query.conversationId) params.set('conversation_id', query.conversationId)
  if (query.status) params.set('status', query.status)
  params.set('limit', String(query.limit ?? 50))
  const data = await apiGet<{ runs: Run[] }>(`/api/runs?${params.toString()}`)
  return data.runs
}

export async function getRun(runId: string): Promise<Run> {
  const data = await apiGet<{ run: Run }>(`/api/runs/${runId}`)
  return data.run
}

export async function cancelRun(runId: string): Promise<Run> {
  const data = await apiPost<{ run: Run }>(`/api/runs/${runId}/cancel`)
  return data.run
}

export async function recoverRun(
  runId: string,
): Promise<{ recovered_from_run_id: string; run: Run; result: AgentResult | null }> {
  return apiPost<{
    recovered_from_run_id: string
    run: Run
    result: AgentResult | null
  }>(`/api/runs/${runId}/recover`)
}

export async function getRunTrace(
  runId: string,
): Promise<{ run: AgentRunTrace; events: AgentEvent[] }> {
  return apiGet<{ run: AgentRunTrace; events: AgentEvent[] }>(
    `/api/runs/${runId}/trace`,
  )
}
