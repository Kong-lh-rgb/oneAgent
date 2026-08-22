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

  it('不展示 Provider 原始 reasoning，只展示正文和结构化过程', () => {
    const html = renderToStaticMarkup(
      <LiveAgentTurn
        runId="run-1"
        step={1}
        events={[event({
          type: 'model_completed',
          message: {
            role: 'assistant',
            content: '回复',
            reasoning: '内部完整推理',
            tool_calls: [],
          },
        })]}
        streamText="回复"
      />,
    )
    expect(html).toContain('回复')
    expect(html).not.toContain('内部完整推理')
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
    expect(html).toContain('已输入 “测试”')
  })

  it('sandbox 审批等待显示中文确认提示', () => {
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
    expect(html).toContain('等待你的确认')
  })

  it('desktop 审批等待显示中文电脑操作提示', () => {
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
    expect(html).toContain('等待电脑操作确认')
  })

  it('computer 操作未验证时显示中文验证状态', () => {
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
    expect(html).toContain('操作已发送 · 等待验证')
  })

  it('usage footer 使用中文字段显示步骤、操作、用量和耗时', () => {
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
            usage: {
              input_tokens: 500,
              output_tokens: 300,
              total_tokens: 800,
              cached_input_tokens: 400,
            },
          }),
          event({ type: 'agent_completed', event_time: '2026-08-20T00:00:10+00:00' }),
        ]}
      />,
    )
    expect(html).toContain('turn-usage')
    expect(html).toContain('第 1 步')
    expect(html).toContain('0 次操作')
    expect(html).toContain('输入 500')
    expect(html).toContain('输出 300')
    expect(html).toContain('缓存 80%')
    expect(html).toContain('10.0s')
  })

  it('Provider 未报告缓存细分时明确显示暂无', () => {
    const html = renderToStaticMarkup(
      <LiveAgentTurn
        runId="run-1"
        step={1}
        events={[
          event({
            type: 'model_completed',
            step: 1,
            usage: { input_tokens: 500, output_tokens: 20, total_tokens: 520 },
          }),
        ]}
      />,
    )

    expect(html).toContain('缓存 暂无')
  })

  it('完成后不重复显示右侧完成状态（仅保留 data-status）', () => {
    const html = renderToStaticMarkup(
      <LiveAgentTurn
        runId="run-1"
        step={2}
        events={[
          event({ type: 'agent_started' }),
          event({
            type: 'agent_completed',
            stop_reason: 'final_answer',
          }),
        ]}
      />,
    )
    expect(html).toContain('data-status="completed"')
    expect(html).not.toContain('agent-turn__status')
    expect(html).not.toContain('已完成')
  })

  it.each([
    ['failed', 'agent_failed', '执行已停止'],
    ['interrupted', 'agent_failed', '执行已中断'],
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
