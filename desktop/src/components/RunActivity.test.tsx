/** Activity：人类可读事件描述 + 列表渲染测试。 */

import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import type { AgentEvent } from '../api/types'
import {
  ActivityItems,
  ActivityTechnicalDetails,
  RunInspectorOverview,
  buildActivityEntries,
  describeActivity,
} from './RunActivity'

function event(partial: Partial<AgentEvent>): AgentEvent {
  return {
    event_id: 'evt-1',
    run_id: 'run-1',
    conversation_id: null,
    sequence: 1,
    type: 'agent_started',
    event_time: '2026-08-20T00:00:00+00:00',
    step: null,
    provider: null,
    model: null,
    message: null,
    tool_call: null,
    tool_result: null,
    usage: null,
    stop_reason: null,
    approval_decision: null,
    ...partial,
  }
}

describe('describeActivity', () => {
  it('工具事件转人话', () => {
    expect(
      describeActivity(
        event({
          type: 'tool_started',
          tool_call: { id: 'call-1', name: 'read_file', arguments: {} },
        }),
      ),
    ).toBe('运行 read_file')
    expect(
      describeActivity(
        event({
          type: 'tool_completed',
          tool_call: { id: 'call-1', name: 'read_file', arguments: {} },
          tool_result: {
            tool_call_id: 'call-1',
            tool_name: 'read_file',
            success: true,
            output: '...',
            error: null,
            duration_ms: 12,
          },
        }),
      ),
    ).toBe('完成 read_file')
    expect(
      describeActivity(
        event({
          type: 'tool_completed',
          tool_result: {
            tool_call_id: 'call-1',
            tool_name: 'bash',
            success: false,
            output: null,
            error: 'boom',
            duration_ms: 3,
          },
        }),
      ),
    ).toBe('失败 bash')
  })

  it('审批 / 完成 / 失败事件', () => {
    expect(
      describeActivity(
        event({
          type: 'tool_approval_required',
          tool_call: { id: 'c', name: 'run_command', arguments: {} },
        }),
      ),
    ).toContain('等待审批')
    expect(describeActivity(event({ type: 'agent_completed' }))).toBe('执行完成')
    expect(
      describeActivity(event({ type: 'agent_failed', stop_reason: 'model_error' })),
    ).toBe('执行失败')
  })
})

describe('ActivityItems', () => {
  it('空状态', () => {
    const html = renderToStaticMarkup(<ActivityItems events={[]} />)
    expect(html).toContain('暂无活动')
  })

  it('把同一个工具的开始与完成事件合成一条记录', () => {
    const events: AgentEvent[] = [
      event({
        event_id: 'e1',
        sequence: 1,
        type: 'tool_started',
        tool_call: { id: 'c1', name: 'read_file', arguments: { path: 'README.md' } },
      }),
      event({
        event_id: 'e2',
        sequence: 2,
        type: 'tool_completed',
        tool_call: { id: 'c1', name: 'read_file', arguments: {} },
        tool_result: {
          tool_call_id: 'c1',
          tool_name: 'read_file',
          success: true,
          output: 'ok',
          error: null,
          duration_ms: 5,
        },
      }),
    ]
    const entries = buildActivityEntries(events)
    expect(entries).toHaveLength(1)
    expect(entries[0]).toMatchObject({
      id: 'c1',
      label: '已读取文件',
      meta: 'read_file',
      state: 'done',
    })

    const html = renderToStaticMarkup(<ActivityItems events={events} />)
    expect(html.match(/已读取文件/g)).toHaveLength(1)
    expect(html).toContain('activity-item--done')
    expect(html).not.toContain('activity-item--active')
  })

  it('Technical details 默认折叠但保留原始分析证据', () => {
    const events = [event({ event_id: 'raw-1', provider: 'fake', model: 'test-model' })]
    const html = renderToStaticMarkup(<ActivityTechnicalDetails events={events} />)
    expect(html).toContain('<details class="activity-details activity-section">')
    expect(html).not.toContain('<details class="activity-details activity-section" open="">')
    expect(html).toContain('技术详情')
    expect(html).toContain('test-model')
  })

  it('审批完成进入执行时间线，不让等待状态成为最后一条证据', () => {
    const events = [
      event({ type: 'tool_approval_required', tool_call: { id: 'c1', name: 'computer_type', arguments: {} } }),
      event({ event_id: 'approved', sequence: 2, type: 'tool_approval_completed', approval_decision: 'approved', tool_call: { id: 'c1', name: 'computer_type', arguments: {} } }),
    ]
    const entries = buildActivityEntries(events)
    expect(entries.map((entry) => entry.label)).toEqual(['等待你的审批', '审批已通过'])
    expect(entries.at(-1)?.state).toBe('done')
  })

  it('浮窗只展示最近活动，并保留 Usage、Context 和 Trace 摘要', () => {
    const events = [
      event({ event_id: 'started', sequence: 1, type: 'agent_started' }),
      event({
        event_id: 'model',
        sequence: 2,
        type: 'model_started',
        step: 1,
        prepared_input_tokens: 12_000,
        working_input_budget: 24_000,
        tool_result_tokens_before: 8_000,
        tool_result_tokens_after: 2_000,
        compaction_stage: 'tool_results',
      }),
      ...Array.from({ length: 6 }, (_, index) => event({
        event_id: `tool-${index}`,
        sequence: index + 3,
        type: 'tool_started',
        tool_call: { id: `call-${index}`, name: 'read_file', arguments: {} },
      })),
    ]
    const html = renderToStaticMarkup(
      <RunInspectorOverview
        events={events}
        usageSummary={{
          main_agent: { input_tokens: 10_000, output_tokens: 500, total_tokens: 10_500, model_calls: 2 },
          memory_reflection: { input_tokens: 0, output_tokens: 0, total_tokens: 0 },
          memory_maintenance: { input_tokens: 0, output_tokens: 0, total_tokens: 0 },
          provider_total: { input_tokens: 10_000, output_tokens: 500, total_tokens: 10_500, model_calls: 2 },
          tool_schema_tokens_estimated: 3_000,
          memory_reflection_status: 'not_run',
          memory_reflection_skip_reason: null,
          main_agent_chargeable_tokens: 2_500,
          run_budget_status: 'active',
          run_budget_reason: null,
          run_budget_warning_tokens: 50_000,
          run_budget_finalization_tokens: 75_000,
          run_budget_hard_tokens: 100_000,
          run_budget_warning_model_calls: 8,
          run_budget_finalization_model_calls: 10,
          run_budget_hard_model_calls: 12,
        }}
      />,
    )

    expect(html).toContain('预算计入')
    expect(html).toContain('12k')
    expect(html).toContain('8k')
    expect(html).toContain('2k')
    expect(html).toContain('Post-Run')
    expect(html).toContain('暂无后台处理')
    expect(html).toContain('另有 2 条活动')
    expect(html.match(/activity-item activity-item--active/g)).toHaveLength(5)
  })

  it('没有工具结果和压缩时保留字段并显示空状态', () => {
    const html = renderToStaticMarkup(
      <RunInspectorOverview
        events={[
          event({
            type: 'model_started',
            step: 1,
            prepared_input_tokens: 1_000,
            working_input_budget: 16_000,
            tool_result_tokens_before: 0,
            tool_result_tokens_after: 0,
            compaction_stage: 'none',
          }),
        ]}
      />,
    )
    expect(html).toContain('Tool results')
    expect(html).toContain('暂无工具结果')
    expect(html).toContain('Compaction')
    expect(html).toContain('暂无压缩')
  })
})
