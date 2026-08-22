/** Run 分析展示层：Context 构成、成本解释、事件合并与 Trace 分组。 */

import { describe, expect, it } from 'vitest'

import type { AgentEvent, Run } from '../api/types'
import {
  buildContextSteps,
  buildTraceGroups,
  latestRunId,
  mergeRunEvents,
} from './runAnalysis'

function event(partial: Partial<AgentEvent>): AgentEvent {
  return {
    event_id: 'e1',
    run_id: 'r1',
    conversation_id: 'c1',
    sequence: 1,
    type: 'model_started',
    event_time: '2026-08-22T00:00:00Z',
    step: 1,
    provider: 'fake',
    model: 'fake',
    message: null,
    tool_call: null,
    tool_result: null,
    usage: null,
    stop_reason: null,
    approval_decision: null,
    ...partial,
  }
}

describe('buildContextSteps', () => {
  it('按 Model Step 构建输入变化、窗口占比与不重复计数的 breakdown', () => {
    const [step] = buildContextSteps([
      event({
        original_estimated_input_tokens: 12_400,
        prepared_input_tokens: 8_100,
        context_window: 128_000,
        input_budget: 24_000,
        working_input_budget: 16_000,
        trigger_tokens: 12_000,
        target_tokens: 8_000,
        prepared_usage_ratio: 0.3375,
        message_tokens_before: 8_700,
        message_tokens_after: 4_700,
        tool_schema_tokens: 3_200,
        tool_result_tokens_before: 6_800,
        tool_result_tokens_after: 2_100,
        skill_catalog_tokens: 300,
        active_skill_tokens: 500,
        compaction_stage: 'tool_results_and_rounds',
        compacted_tool_results: 4,
        removed_tool_rounds: 3,
        summary_updated: true,
      }),
    ])
    expect(step.originalInputTokens).toBe(12_400)
    expect(step.preparedInputTokens).toBe(8_100)
    expect(step.windowUsageRatio).toBeCloseTo(8_100 / 128_000)
    expect(step.budgetUsageRatio).toBeCloseTo(8_100 / 16_000)
    expect(step.breakdown.find((item) => item.key === 'messages')?.tokens).toBe(1_800)
    expect(step.breakdown.reduce((sum, item) => sum + item.tokens, 0)).toBe(8_100)
    expect(step.removedToolRounds).toBe(3)
    expect(step.summaryUpdated).toBe(true)
  })
})

describe('trace', () => {
  it('durable 与 live 按 event_id 去重并按 sequence 排序', () => {
    const first = event({ event_id: 'e1', sequence: 1 })
    const second = event({ event_id: 'e2', sequence: 2, step: 2 })
    expect(mergeRunEvents([first], [second, first]).map((item) => item.event_id)).toEqual(['e1', 'e2'])
    expect(buildTraceGroups([first, second]).map((group) => group.label)).toEqual(['Step 1', 'Step 2'])
  })

  it('从持久化列表恢复最新Run，不依赖返回顺序', () => {
    const base: Run = {
      id: 'old',
      conversation_id: 'c1',
      status: 'completed',
      user_message: 'old',
      created_at: '2026-08-21T00:00:00Z',
      started_at: null,
      updated_at: '2026-08-21T00:00:00Z',
      completed_at: null,
      error: null,
      stop_reason: 'final_answer',
      recovered_from_run_id: null,
      source: null,
      source_id: null,
      scheduled_for: null,
      triggered_at: null,
      mode: 'normal',
    }
    const newest = {
      ...base,
      id: 'newest',
      created_at: '2026-08-22T00:00:00Z',
    }
    expect(latestRunId([newest, base])).toBe('newest')
    expect(latestRunId([])).toBeNull()
  })
})
