/** ComputerApprovalPopup：电脑审批共享逻辑（分类 / 浮动卡片）测试。 */

import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import type { ApprovalRequest } from '../api/types'
import { isComputerApproval } from './ComputerApprovalPopup'
import { ApprovalFloatingCard } from './ComputerApprovalPopup'

const computerApproval: ApprovalRequest = {
  id: 'appr-computer-1',
  run_id: 'run-1',
  conversation_id: 'conv-1',
  tool_name: 'computer_type',
  tool_call_id: 'call-1',
  arguments: { text: '测试' },
  reason: '需要输入文本',
  status: 'pending',
  created_at: '2026-08-21T00:00:00+00:00',
  resolved_at: null,
}

describe('isComputerApproval', () => {
  it('computer_* 工具 → true', () => {
    expect(
      isComputerApproval({ ...computerApproval, tool_name: 'computer_click' }),
    ).toBe(true)
    expect(
      isComputerApproval({ ...computerApproval, tool_name: 'computer_type' }),
    ).toBe(true)
    expect(
      isComputerApproval({ ...computerApproval, tool_name: 'computer_key' }),
    ).toBe(true)
  })

  it('非 computer 工具 → false', () => {
    expect(
      isComputerApproval({ ...computerApproval, tool_name: 'run_shell_command' }),
    ).toBe(false)
    expect(
      isComputerApproval({ ...computerApproval, tool_name: 'http_request' }),
    ).toBe(false)
  })
})

describe('ApprovalFloatingCard', () => {
  it('computer 审批 → 浮窗可见，展示人类可读动作与 Allow/Deny', () => {
    const html = renderToStaticMarkup(
      <ApprovalFloatingCard
        approval={computerApproval}
        onApprove={() => {}}
        onDeny={() => {}}
      />,
    )
    expect(html).toContain('approval-floating-card')
    expect(html).toContain('Permission required')
    expect(html).toContain('Type text')
    expect(html).toContain('“测试”')
    expect(html).toContain('Show details')
    expect(html).toContain('Allow')
    expect(html).toContain('Deny')
    // 技术 tool_name 不直接作为主标题。
    expect(html).toContain('computer_type')
  })

  it('有排队审批时显示 "N more waiting"', () => {
    const html = renderToStaticMarkup(
      <ApprovalFloatingCard
        approval={computerApproval}
        queuedCount={3}
        onApprove={() => {}}
        onDeny={() => {}}
      />,
    )
    expect(html).toContain('2 more waiting')
  })

  it('无排队时不显示 waiting', () => {
    const html = renderToStaticMarkup(
      <ApprovalFloatingCard
        approval={computerApproval}
        queuedCount={1}
        onApprove={() => {}}
        onDeny={() => {}}
      />,
    )
    expect(html).not.toContain('more waiting')
  })

  it('busy 时按钮禁用（防双击）', () => {
    const html = renderToStaticMarkup(
      <ApprovalFloatingCard
        approval={computerApproval}
        busy
        onApprove={() => {}}
        onDeny={() => {}}
      />,
    )
    expect(html).toContain('disabled=""')
  })

  it('RPC 失败 → 审批保持可见并显示错误', () => {
    const html = renderToStaticMarkup(
      <ApprovalFloatingCard
        approval={computerApproval}
        error="approve failed"
        onApprove={() => {}}
        onDeny={() => {}}
      />,
    )
    expect(html).toContain('approve failed')
    // 审批仍在（卡片仍渲染 Allow/Deny）。
    expect(html).toContain('Allow')
  })

  it('批准后显示执行状态，不再显示审批按钮', () => {
    const html = renderToStaticMarkup(
      <ApprovalFloatingCard
        approval={computerApproval}
        phase="executing"
        onApprove={() => {}}
        onDeny={() => {}}
      />,
    )
    expect(html).toContain('Executing')
    expect(html).toContain('performing the approved computer action')
    expect(html).not.toContain('>Allow<')
    expect(html).not.toContain('>Deny<')
  })

  it('动作投递后使用诚实文案，不声称界面效果已验证', () => {
    const html = renderToStaticMarkup(
      <ApprovalFloatingCard
        approval={computerApproval}
        phase="action_delivered"
        onApprove={() => {}}
        onDeny={() => {}}
      />,
    )
    expect(html).toContain('Action sent')
    expect(html).toContain('checking the result')
    expect(html).not.toContain('Text entered')
  })

  it('Run 失败时显示停止原因', () => {
    const html = renderToStaticMarkup(
      <ApprovalFloatingCard
        approval={computerApproval}
        phase="run_failed"
        error="maximum step limit reached"
        onApprove={() => {}}
        onDeny={() => {}}
      />,
    )
    expect(html).toContain('Run stopped')
    expect(html).toContain('maximum step limit reached')
  })
})
