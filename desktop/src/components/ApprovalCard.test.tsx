/** ApprovalCard：统一审批卡片渲染测试。 */

import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import type { ApprovalRequest } from '../api/types'
import ApprovalCard from './ApprovalCard'

const approval: ApprovalRequest = {
  id: 'appr-123456',
  run_id: 'run-1',
  conversation_id: 'conv-1',
  tool_name: 'run_command',
  tool_call_id: 'call-abc',
  arguments: { command: 'ls -la' },
  reason: '需要执行 shell 命令',
  status: 'pending',
  created_at: '2026-08-20T00:00:00+00:00',
  resolved_at: null,
}

describe('ApprovalCard', () => {
  it('展示工具名与 reason', () => {
    const html = renderToStaticMarkup(
      <ApprovalCard approval={approval} onApprove={() => {}} onDeny={() => {}} />,
    )
    expect(html).toContain('run_command')
    expect(html).toContain('需要执行 shell 命令')
    expect(html).toContain('approval-card')
  })

  it('提供 Approve / Deny 按钮', () => {
    const html = renderToStaticMarkup(
      <ApprovalCard approval={approval} onApprove={() => {}} onDeny={() => {}} />,
    )
    expect(html).toContain('Approve')
    expect(html).toContain('Deny')
  })

  it('busy 时按钮禁用', () => {
    const html = renderToStaticMarkup(
      <ApprovalCard approval={approval} busy onApprove={() => {}} onDeny={() => {}} />,
    )
    expect(html).toContain('disabled=""')
  })
})
