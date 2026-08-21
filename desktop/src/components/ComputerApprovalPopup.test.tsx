/** ComputerApprovalPopup：电脑审批共享逻辑（过滤 / 浮动卡片）测试。 */

import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import type { ApprovalRequest } from '../api/types'
import {
  ApprovalFloatingCard,
  isComputerApproval,
} from './ComputerApprovalPopup'

const computerApproval: ApprovalRequest = {
  id: 'appr-computer-1',
  run_id: 'run-1',
  conversation_id: 'conv-1',
  tool_name: 'computer_click',
  tool_call_id: 'call-1',
  arguments: { observation_id: 'obs-1', element_ref: 'e1' },
  reason: '需要点击界面元素',
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
      isComputerApproval({ ...computerApproval, tool_name: 'run_command' }),
    ).toBe(false)
    expect(
      isComputerApproval({ ...computerApproval, tool_name: 'web_search' }),
    ).toBe(false)
  })
})

describe('ApprovalFloatingCard', () => {
  it('渲染浮窗卡片与审批选项', () => {
    const html = renderToStaticMarkup(
      <ApprovalFloatingCard
        approval={computerApproval}
        onApprove={() => {}}
        onDeny={() => {}}
      />,
    )
    expect(html).toContain('approval-floating-card')
    expect(html).toContain('computer_click')
    expect(html).toContain('Approve')
    expect(html).toContain('Deny')
    expect(html).toContain('Computer action approval')
  })

  it('有排队审批时显示排队数', () => {
    const html = renderToStaticMarkup(
      <ApprovalFloatingCard
        approval={computerApproval}
        queuedCount={3}
        onApprove={() => {}}
        onDeny={() => {}}
      />,
    )
    expect(html).toContain('2 queued')
  })

  it('无排队时不显示排队徽章', () => {
    const html = renderToStaticMarkup(
      <ApprovalFloatingCard
        approval={computerApproval}
        queuedCount={1}
        onApprove={() => {}}
        onDeny={() => {}}
      />,
    )
    expect(html).not.toContain('queued')
  })

  it('显示错误信息', () => {
    const html = renderToStaticMarkup(
      <ApprovalFloatingCard
        approval={computerApproval}
        error="approve failed"
        onApprove={() => {}}
        onDeny={() => {}}
      />,
    )
    expect(html).toContain('approve failed')
  })
})
