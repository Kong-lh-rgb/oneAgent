/** Run 分析展示层：集中解释 Context、成本与 Trace，不让组件理解事件协议。 */

import type { AgentEvent } from '../api/types'

export interface ContextBreakdownItem {
  key: 'messages' | 'tool_schemas' | 'tool_results' | 'skills' | 'other'
  label: string
  tokens: number
  ratio: number
}

export interface ContextStepVM {
  step: number
  eventTime: string
  originalInputTokens: number
  preparedInputTokens: number
  contextWindow: number
  inputBudget: number
  workingInputBudget: number
  triggerTokens: number
  targetTokens: number
  windowUsageRatio: number
  budgetUsageRatio: number
  messageTokensBefore: number
  messageTokensAfter: number
  toolSchemaTokens: number
  toolResultTokensBefore: number
  toolResultTokensAfter: number
  skillTokens: number
  compactionStage: string
  compactedToolResults: number
  removedToolRounds: number
  summaryUpdated: boolean
  summaryError: string | null
  reachedTarget: boolean | null
  breakdown: ContextBreakdownItem[]
}

export interface TraceGroupVM {
  id: string
  label: string
  events: AgentEvent[]
}

function numberOrZero(value: number | null | undefined): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

export function mergeRunEvents(
  durable: AgentEvent[],
  live: AgentEvent[],
): AgentEvent[] {
  const byId = new Map<string, AgentEvent>()
  for (const event of [...durable, ...live]) byId.set(event.event_id, event)
  return [...byId.values()].sort((a, b) => {
    if (a.sequence !== b.sequence) return a.sequence - b.sequence
    return a.event_time.localeCompare(b.event_time)
  })
}

export function buildContextSteps(events: AgentEvent[]): ContextStepVM[] {
  return events
    .filter((event) => event.type === 'model_started' && event.step != null)
    .map((event) => {
      const prepared = numberOrZero(
        event.prepared_input_tokens ?? event.estimated_input_tokens,
      )
      const original = numberOrZero(
        event.original_estimated_input_tokens ?? prepared,
      )
      const contextWindow = numberOrZero(event.context_window)
      const workingInputBudget = numberOrZero(event.working_input_budget)
      const allMessageTokens = numberOrZero(event.message_tokens_after)
      const schemas = numberOrZero(event.tool_schema_tokens)
      const toolResults = numberOrZero(event.tool_result_tokens_after)
      const skills = numberOrZero(event.skill_catalog_tokens)
        + numberOrZero(event.active_skill_tokens)
      // message_tokens_after 已包含 tool result 与 Skill 注入，必须扣除后再分栏，
      // 否则 breakdown 会重复计数。
      const messages = Math.max(0, allMessageTokens - toolResults - skills)
      const known = messages + schemas + toolResults + skills
      const other = Math.max(0, prepared - known)
      const rawBreakdown = [
        { key: 'messages' as const, label: 'Messages & injected', tokens: messages },
        { key: 'tool_schemas' as const, label: 'Tool schemas', tokens: schemas },
        { key: 'tool_results' as const, label: 'Tool results', tokens: toolResults },
        { key: 'skills' as const, label: 'Skills', tokens: skills },
        { key: 'other' as const, label: 'Request overhead', tokens: other },
      ]
      return {
        step: event.step!,
        eventTime: event.event_time,
        originalInputTokens: original,
        preparedInputTokens: prepared,
        contextWindow,
        inputBudget: numberOrZero(event.input_budget),
        workingInputBudget,
        triggerTokens: numberOrZero(event.trigger_tokens),
        targetTokens: numberOrZero(event.target_tokens),
        windowUsageRatio: contextWindow > 0 ? prepared / contextWindow : 0,
        budgetUsageRatio: workingInputBudget > 0
          ? prepared / workingInputBudget
          : (event.prepared_usage_ratio ?? event.usage_ratio ?? 0),
        messageTokensBefore: numberOrZero(event.message_tokens_before),
        messageTokensAfter: allMessageTokens,
        toolSchemaTokens: schemas,
        toolResultTokensBefore: numberOrZero(event.tool_result_tokens_before),
        toolResultTokensAfter: toolResults,
        skillTokens: skills,
        compactionStage: event.compaction_stage ?? 'none',
        compactedToolResults: numberOrZero(event.compacted_tool_results),
        removedToolRounds: numberOrZero(event.removed_tool_rounds),
        summaryUpdated: event.summary_updated === true,
        summaryError: event.summary_error ?? null,
        reachedTarget: event.reached_target ?? null,
        breakdown: rawBreakdown.map((item) => ({
          ...item,
          ratio: prepared > 0 ? item.tokens / prepared : 0,
        })),
      }
    })
}

/** 用 Runtime 事实解释成本，不调用模型、不猜测业务原因。 */
export function explainRunCost(events: AgentEvent[]): string[] {
  const steps = buildContextSteps(events)
  if (steps.length === 0) return []
  const reasons: string[] = []
  const totalSchemas = steps.reduce((sum, step) => sum + step.toolSchemaTokens, 0)
  const peakToolResults = Math.max(...steps.map((step) => step.toolResultTokensBefore))
  const first = steps[0]
  const last = steps.at(-1)!
  if (steps.length > 1 && totalSchemas > 0) {
    reasons.push(`${steps.length} 次模型请求累计携带了约 ${totalSchemas} 个 Tool Schema tokens。`)
  }
  if (peakToolResults > 0) {
    reasons.push(`工具结果在压缩前最高达到约 ${peakToolResults} tokens。`)
  }
  if (last.originalInputTokens > first.originalInputTokens) {
    reasons.push(`输入上下文从约 ${first.originalInputTokens} 增长到 ${last.originalInputTokens} tokens。`)
  }
  const firstCompaction = steps.find((step) => step.compactionStage !== 'none')
  if (firstCompaction) reasons.push(`上下文压缩从第 ${firstCompaction.step} 步开始生效。`)
  return reasons
}

export function buildTraceGroups(events: AgentEvent[]): TraceGroupVM[] {
  const groups = new Map<string, AgentEvent[]>()
  for (const event of events) {
    const key = event.step == null ? 'run' : `step-${event.step}`
    const list = groups.get(key) ?? []
    list.push(event)
    groups.set(key, list)
  }
  return [...groups.entries()].map(([id, grouped]) => ({
    id,
    label: id === 'run' ? 'Run lifecycle' : `Step ${id.slice(5)}`,
    events: grouped,
  }))
}
