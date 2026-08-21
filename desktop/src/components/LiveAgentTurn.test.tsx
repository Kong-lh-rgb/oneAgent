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
  it('无事件时显示中文等待提示', () => {
    const html = renderToStaticMarkup(<LiveAgentTurn events={[]} streamText="" />)
    expect(html).toContain('Vesta')
    expect(html).toContain('live-turn__pulse')
    expect(html).toContain('正在执行…')
    expect(html).toContain('live-turn__waiting')
  })

  it('不再内联展示英文工具活动，只渲染流式正文', () => {
    const html = renderToStaticMarkup(
      <LiveAgentTurn
        events={[
          event({
            type: 'tool_started',
            tool_call: { id: 't1', name: 'computer_observe', arguments: {} },
          }),
        ]}
        streamText="正在生成 **报告**"
      />,
    )
    expect(html).not.toContain('live-turn__activity')
    expect(html).not.toContain('Thinking')
    expect(html).toContain('正在生成')
    expect(html).toContain('<strong>报告</strong>')
    expect(html).toContain('stream-cursor')
  })

  it('展示模型思考过程（reasoning），有正文时默认折叠', () => {
    const html = renderToStaticMarkup(
      <LiveAgentTurn
        events={[event({ type: 'model_completed' })]}
        streamText="回复"
        reasoning="先分析问题，再规划步骤"
      />,
    )
    expect(html).toContain('思考过程')
    expect(html).toContain('先分析问题，再规划步骤')
    expect(html).toContain('class="assistant-reasoning"')
    expect(html).not.toContain('assistant-reasoning--open')
  })

  it('思考进行中（尚无正文）时自动展开并显示“思考中…”', () => {
    const html = renderToStaticMarkup(
      <LiveAgentTurn
        events={[event({ type: 'model_started' })]}
        streamText=""
        reasoning="正在分析需求…"
      />,
    )
    expect(html).toContain('思考中…')
    expect(html).toContain('assistant-reasoning--open')
    expect(html).toContain('assistant-reasoning__spinner')
  })

  it('无 reasoning 时不渲染思考块', () => {
    const html = renderToStaticMarkup(
      <LiveAgentTurn events={[event({ type: 'model_completed' })]} streamText="回复" />,
    )
    expect(html).not.toContain('assistant-reasoning')
  })
})
