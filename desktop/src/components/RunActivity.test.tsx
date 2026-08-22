/** Activity：人类可读事件描述 + 列表渲染测试。 */

import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import type { AgentEvent } from '../api/types'
import {
  ActivityItems,
  ActivityTechnicalDetails,
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
    ).toBe('Running read_file')
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
    ).toBe('Completed read_file')
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
    ).toBe('Failed bash')
  })

  it('审批 / 完成 / 失败事件', () => {
    expect(
      describeActivity(
        event({
          type: 'tool_approval_required',
          tool_call: { id: 'c', name: 'run_command', arguments: {} },
        }),
      ),
    ).toContain('Waiting for approval')
    expect(describeActivity(event({ type: 'agent_completed' }))).toBe('Finished')
    expect(
      describeActivity(event({ type: 'agent_failed', stop_reason: 'model_error' })),
    ).toBe('Run failed')
  })
})

describe('ActivityItems', () => {
  it('空状态', () => {
    const html = renderToStaticMarkup(<ActivityItems events={[]} />)
    expect(html).toContain('No activity yet')
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
      label: 'Read file',
      meta: 'read_file',
      state: 'done',
    })

    const html = renderToStaticMarkup(<ActivityItems events={events} />)
    expect(html.match(/Read file/g)).toHaveLength(1)
    expect(html).toContain('activity-item--done')
    expect(html).not.toContain('activity-item--active')
  })

  it('Technical details 默认折叠但保留原始分析证据', () => {
    const events = [event({ event_id: 'raw-1', provider: 'fake', model: 'test-model' })]
    const html = renderToStaticMarkup(<ActivityTechnicalDetails events={events} />)
    expect(html).toContain('<details class="activity-details activity-section">')
    expect(html).not.toContain('<details class="activity-details activity-section" open="">')
    expect(html).toContain('Technical details')
    expect(html).toContain('test-model')
  })
})
