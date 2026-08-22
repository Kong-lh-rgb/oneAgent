/** Execution Trace：验证分步结构和技术证据完整保留。 */

import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import type { AgentEvent } from '../api/types'
import ExecutionTrace from './ExecutionTrace'

function event(partial: Partial<AgentEvent>): AgentEvent {
  return {
    event_id: 'event-1',
    run_id: 'run-1',
    conversation_id: 'conversation-1',
    sequence: 1,
    type: 'model_started',
    event_time: '2026-08-22T00:00:00Z',
    step: 1,
    provider: 'fake',
    model: 'fake-model',
    message: null,
    tool_call: null,
    tool_result: null,
    usage: null,
    stop_reason: null,
    approval_decision: null,
    ...partial,
  }
}

describe('ExecutionTrace', () => {
  it('按 Step 展示工具、审批和原始 JSON', () => {
    const events = [
      event({ event_id: 'model-1', type: 'model_started' }),
      event({
        event_id: 'approval-1',
        sequence: 2,
        type: 'tool_approval_completed',
        approval_decision: 'approved',
        tool_call: { id: 'call-1', name: 'computer_type', arguments: { text: '你好' } },
      }),
      event({
        event_id: 'tool-1',
        sequence: 3,
        type: 'tool_completed',
        tool_result: {
          tool_call_id: 'call-1',
          tool_name: 'computer_type',
          success: true,
          output: 'delivered',
          error: null,
          duration_ms: 125,
        },
      }),
    ]
    const html = renderToStaticMarkup(<ExecutionTrace events={events} />)
    expect(html).toContain('Step 1')
    expect(html).toContain('tool approval completed')
    expect(html).toContain('computer_type')
    expect(html).toContain('125ms')
    expect(html).toContain('&quot;approval_decision&quot;: &quot;approved&quot;')
  })
})
