/** Context Inspector：验证后端事件数据可以直接形成可解释界面。 */

import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import type { AgentEvent } from '../api/types'
import ContextInspector from './ContextInspector'

function contextEvent(): AgentEvent {
  return {
    event_id: 'context-1',
    run_id: 'run-1',
    conversation_id: 'conversation-1',
    sequence: 1,
    type: 'model_started',
    event_time: '2026-08-22T00:00:00Z',
    step: 2,
    provider: 'fake',
    model: 'fake-model',
    message: null,
    tool_call: null,
    tool_result: null,
    usage: null,
    stop_reason: null,
    approval_decision: null,
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
  }
}

describe('ContextInspector', () => {
  it('展示窗口、预算与压缩动作', () => {
    const html = renderToStaticMarkup(<ContextInspector events={[contextEvent()]} />)
    expect(html).toContain('Model step')
    expect(html).toContain('128k')
    expect(html).toContain('6.3%')
    expect(html).toContain('50.6%')
    expect(html).toContain('Working budget')
    expect(html).toContain('12k / 8k')
    expect(html).toContain('4 个工具结果已压缩')
    expect(html).toContain('3 个旧工具轮已移除')
    expect(html).toContain('Conversation summary 已更新')
    expect(html).toContain('Memory、Task 与系统消息当前没有独立计数字段')
    expect(html).not.toContain('为什么这轮可能更贵')
  })

  it('没有模型请求时显示明确空状态', () => {
    const html = renderToStaticMarkup(<ContextInspector events={[]} />)
    expect(html).toContain('暂无 Context 数据')
  })
})
