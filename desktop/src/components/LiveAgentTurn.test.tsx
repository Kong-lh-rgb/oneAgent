/** LiveAgentTurn：实时执行过程与流式正文测试。 */

import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import type { AgentEvent } from '../api/types'
import LiveAgentTurn from './LiveAgentTurn'

function event(partial: Partial<AgentEvent>): AgentEvent {
  return {
    event_id: 'evt-1',
    run_id: 'run-1',
    conversation_id: 'conv-1',
    sequence: 1,
    type: 'model_started',
    event_time: '2026-08-20T00:00:00+00:00',
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

describe('LiveAgentTurn', () => {
  it('无事件时立即显示启动状态', () => {
    const html = renderToStaticMarkup(<LiveAgentTurn events={[]} streamText="" />)
    expect(html).toContain('oneAgent')
    expect(html).toContain('live-turn__pulse')
    expect(html).toContain('Starting the run')
  })

  it('同时展示可理解活动与模型增量文本', () => {
    const html = renderToStaticMarkup(
      <LiveAgentTurn
        events={[event({ type: 'model_started' })]}
        streamText="正在生成 **报告**"
      />,
    )
    expect(html).toContain('Thinking through the next step')
    expect(html).toContain('正在生成')
    expect(html).toContain('<strong>报告</strong>')
    expect(html).toContain('stream-cursor')
  })
})
