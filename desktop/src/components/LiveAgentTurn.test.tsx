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
    const html = renderToStaticMarkup(<LiveAgentTurn runId="run-1" step={1} events={[]} />)
    expect(html).toContain('Vesta')
    expect(html).toContain('live-turn__pulse')
    expect(html).toContain('正在执行…')
    expect(html).toContain('live-turn__waiting')
  })

  it('不再内联展示英文工具活动，只渲染流式正文', () => {
    const html = renderToStaticMarkup(
      <LiveAgentTurn
        runId="run-1"
        step={1}
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
        runId="run-1"
        step={1}
        events={[event({ type: 'model_completed' })]}
        streamText="回复"
        reasoning="先分析问题，再规划步骤"
      />,
    )
    expect(html).toContain('Thinking')
    expect(html).toContain('先分析问题，再规划步骤')
    expect(html).toContain('class="assistant-reasoning"')
    expect(html).not.toContain('assistant-reasoning--open')
  })

  it('思考进行中（尚无正文）时自动展开并显示 Thinking', () => {
    const html = renderToStaticMarkup(
      <LiveAgentTurn
        runId="run-1"
        step={1}
        events={[event({ type: 'model_started' })]}
        streamText=""
        reasoning="正在分析需求…"
      />,
    )
    expect(html).toContain('Thinking')
    expect(html).toContain('assistant-reasoning--open')
    expect(html).toContain('assistant-reasoning__spinner')
  })

  it('无 reasoning 时不渲染思考块', () => {
    const html = renderToStaticMarkup(
      <LiveAgentTurn runId="run-1" step={1} events={[event({ type: 'model_completed' })]} streamText="回复" />,
    )
    expect(html).not.toContain('assistant-reasoning')
  })

  it('tool timeline 合并 started/completed 为人类可读动作', () => {
    const html = renderToStaticMarkup(
      <LiveAgentTurn
        runId="run-1"
        step={1}
        events={[
          event({
            type: 'tool_started',
            tool_call: { id: 't1', name: 'computer_type', arguments: { text: '测试' } },
          }),
          event({
            type: 'tool_completed',
            tool_call: { id: 't1', name: 'computer_type', arguments: {} },
            tool_result: {
              tool_call_id: 't1',
              tool_name: 'computer_type',
              success: true,
              output: 'ok',
              error: null,
              duration_ms: 10,
            },
          }),
        ]}
      />,
    )
    expect(html).toContain('turn-timeline')
    expect(html).toContain('agent-action--done')
    expect(html).toContain('Typed “测试”')
  })

  it('sandbox 审批等待显示 Waiting for approval', () => {
    const html = renderToStaticMarkup(
      <LiveAgentTurn
        runId="run-1"
        step={1}
        events={[
          event({
            type: 'tool_started',
            tool_call: { id: 't1', name: 'run_shell_command', arguments: {} },
          }),
          event({
            type: 'tool_approval_required',
            tool_call: { id: 't1', name: 'run_shell_command', arguments: {} },
          }),
        ]}
      />,
    )
    expect(html).toContain('agent-action--waiting')
    expect(html).toContain('Waiting for approval')
  })

  it('desktop 审批等待显示 Waiting for desktop approval', () => {
    const html = renderToStaticMarkup(
      <LiveAgentTurn
        runId="run-1"
        step={1}
        events={[
          event({
            type: 'tool_started',
            tool_call: { id: 't1', name: 'computer_type', arguments: {} },
          }),
          event({
            type: 'tool_approval_required',
            tool_call: { id: 't1', name: 'computer_type', arguments: {} },
          }),
        ]}
      />,
    )
    expect(html).toContain('Waiting for desktop approval')
  })

  it('computer 操作未验证时显示 not yet verified', () => {
    const html = renderToStaticMarkup(
      <LiveAgentTurn
        runId="run-1"
        step={1}
        events={[
          event({
            type: 'tool_started',
            tool_call: { id: 't1', name: 'computer_click', arguments: {} },
          }),
          event({
            type: 'tool_completed',
            tool_call: { id: 't1', name: 'computer_click', arguments: {} },
            tool_result: {
              tool_call_id: 't1',
              tool_name: 'computer_click',
              success: true,
              output: '{"verification_status":"unverified"}',
              error: null,
              duration_ms: 5,
            },
          }),
        ]}
      />,
    )
    expect(html).toContain('Action sent · waiting for verification')
  })

  it('usage footer 显示 steps / tools / tokens / duration', () => {
    const html = renderToStaticMarkup(
      <LiveAgentTurn
        runId="run-1"
        step={1}
        events={[
          event({ type: 'agent_started', event_time: '2026-08-20T00:00:00+00:00' }),
          event({ type: 'model_started', step: 1 }),
          event({
            type: 'model_completed',
            step: 1,
            usage: { input_tokens: 500, output_tokens: 300, total_tokens: 800 },
          }),
          event({ type: 'agent_completed', event_time: '2026-08-20T00:00:10+00:00' }),
        ]}
      />,
    )
    expect(html).toContain('turn-usage')
    expect(html).toContain('1 step')
    expect(html).toContain('500 in')
    expect(html).toContain('300 out')
    expect(html).toContain('10.0s')
  })

  it.each([
    ['completed', 'agent_completed', 'Completed'],
    ['failed', 'agent_failed', 'Stopped'],
    ['interrupted', 'agent_failed', 'Interrupted'],
  ] as const)('持久展示 %s 终态', (status, type, label) => {
    const html = renderToStaticMarkup(
      <LiveAgentTurn
        runId="run-1"
        step={2}
        events={[
          event({ type: 'agent_started' }),
          event({
            type,
            stop_reason: status === 'interrupted' ? 'interrupted' : status === 'failed' ? 'model_error' : 'final_answer',
          }),
        ]}
      />,
    )
    expect(html).toContain(`data-status="${status}"`)
    expect(html).toContain(label)
  })
})
