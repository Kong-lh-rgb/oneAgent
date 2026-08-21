/** 审批路由与展示纯逻辑测试。 */

import { describe, expect, it } from 'vitest'

import type { ApprovalRequest } from '../api/types'
import {
  chatShouldShowApproval,
  computerActionDescription,
  computerActionLabel,
  computerActionSummary,
  floatingShouldShowApproval,
  formatKeyShortcut,
  isChatApproval,
  isComputerApproval,
  isDesktopApproval,
  isSandboxApproval,
  maybePushComputerApproval,
  maybePushDesktopApproval,
  pushApproval,
  removeApproval,
} from './computerApproval'

function approval(overrides: Partial<ApprovalRequest>): ApprovalRequest {
  return {
    id: 'appr-1',
    run_id: 'run-1',
    conversation_id: 'conv-1',
    tool_name: 'computer_click',
    tool_call_id: 'call-1',
    arguments: {},
    reason: '',
    status: 'pending',
    created_at: '2026-08-21T00:00:00+00:00',
    resolved_at: null,
    ...overrides,
  }
}

describe('isComputerApproval', () => {
  it('computer_* 工具 → true', () => {
    for (const name of [
      'computer_click',
      'computer_type',
      'computer_key',
      'computer_scroll',
      'computer_open_app',
      'computer_focus_window',
    ]) {
      expect(isComputerApproval(approval({ tool_name: name }))).toBe(true)
    }
  })

  it('普通工具 → false', () => {
    expect(
      isComputerApproval(approval({ tool_name: 'run_shell_command' })),
    ).toBe(false)
    expect(isComputerApproval(approval({ tool_name: 'http_request' }))).toBe(false)
  })
})

describe('isDesktopApproval / isSandboxApproval（声明式 ui_scope）', () => {
  it('ui_scope=desktop → desktop（即使名字不带 computer_）', () => {
    expect(
      isDesktopApproval(approval({ tool_name: 'some_tool', ui_scope: 'desktop' })),
    ).toBe(true)
    expect(
      isSandboxApproval(approval({ tool_name: 'some_tool', ui_scope: 'desktop' })),
    ).toBe(false)
  })

  it('ui_scope=sandbox → 非 desktop（即使名字带 computer_ 前缀）', () => {
    expect(
      isDesktopApproval(approval({ tool_name: 'computer_hack', ui_scope: 'sandbox' })),
    ).toBe(false)
    expect(
      isSandboxApproval(approval({ tool_name: 'computer_hack', ui_scope: 'sandbox' })),
    ).toBe(true)
  })

  it('ui_scope 缺失 → 回退 computer_ 前缀', () => {
    expect(isDesktopApproval(approval({ tool_name: 'computer_type' }))).toBe(true)
    expect(
      isDesktopApproval(approval({ tool_name: 'run_shell_command' })),
    ).toBe(false)
  })
})

describe('稳定路由：chatShouldShowApproval / floatingShouldShowApproval', () => {
  const sandbox = approval({ id: 's1', tool_name: 'run_shell_command' })
  const desktop = approval({ id: 'd1', tool_name: 'computer_type', ui_scope: 'desktop' })

  it('sandbox + 属于当前 Run → 进入 Chat', () => {
    expect(chatShouldShowApproval(sandbox, 'run-1')).toBe(true)
  })

  it('desktop 无论主窗口状态都不进入 Chat', () => {
    expect(chatShouldShowApproval(desktop, 'run-1')).toBe(false)
  })

  it('不属于当前 Run → 不进 Chat', () => {
    expect(chatShouldShowApproval(sandbox, 'run-other')).toBe(false)
  })

  it('浮窗：desktop 始终显示', () => {
    expect(floatingShouldShowApproval(desktop)).toBe(true)
  })

  it('浮窗：sandbox 永远不显示', () => {
    expect(floatingShouldShowApproval(sandbox)).toBe(false)
  })

  it('desktop 审批只归浮窗，不随焦点迁移 surface', () => {
    expect(chatShouldShowApproval(desktop, 'run-1')).toBe(false)
    expect(floatingShouldShowApproval(desktop)).toBe(true)
  })
})

describe('isChatApproval', () => {
  const normal = approval({ tool_name: 'run_shell_command' })
  const computer = approval({ tool_name: 'computer_type' })

  it('普通 + 属于当前 Run → true', () => {
    expect(isChatApproval(normal, 'run-1')).toBe(true)
  })

  it('普通 + 不属于当前 Run → false', () => {
    expect(isChatApproval(normal, 'run-other')).toBe(false)
    expect(isChatApproval(normal, null)).toBe(false)
  })

  it('computer 审批即使属于当前 Run 也不进 Chat → false', () => {
    expect(isChatApproval(computer, 'run-1')).toBe(false)
  })
})

describe('动作文案', () => {
  it('computer 工具 → 人类可读动作名', () => {
    expect(computerActionLabel(approval({ tool_name: 'computer_click' }))).toBe(
      'Click an interface element',
    )
    expect(computerActionLabel(approval({ tool_name: 'computer_type' }))).toBe(
      'Type text',
    )
    expect(computerActionLabel(approval({ tool_name: 'computer_key' }))).toBe(
      'Press a key or shortcut',
    )
  })

  it('未知 computer_* → Control this Mac', () => {
    expect(computerActionLabel(approval({ tool_name: 'computer_hack' }))).toBe(
      'Control this Mac',
    )
  })

  it('描述句', () => {
    expect(
      computerActionDescription(approval({ tool_name: 'computer_type' })),
    ).toContain('type text')
  })
})

describe('参数摘要', () => {
  it('computer_type → 引号文本', () => {
    expect(
      computerActionSummary(
        approval({ tool_name: 'computer_type', arguments: { text: '测试' } }),
      ),
    ).toBe('“测试”')
  })

  it('computer_key → 快捷键', () => {
    expect(
      computerActionSummary(
        approval({
          tool_name: 'computer_key',
          arguments: { key: 'n', modifiers: ['command'] },
        }),
      ),
    ).toBe('⌘ N')
  })

  it('computer_click element_ref → 摘要', () => {
    expect(
      computerActionSummary(
        approval({
          tool_name: 'computer_click',
          arguments: { element_ref: 'e102' },
        }),
      ),
    ).toBe('Element e102')
  })

  it('computer_click 坐标 → 摘要', () => {
    expect(
      computerActionSummary(
        approval({
          tool_name: 'computer_click',
          arguments: { x: 1100, y: 300 },
        }),
      ),
    ).toBe('At position (1100, 300)')
  })

  it('无可展示内容 → null', () => {
    expect(computerActionSummary(approval({ tool_name: 'computer_focus_window', arguments: {} }))).toBeNull()
  })
})

describe('formatKeyShortcut', () => {
  it('modifier 符号 + 键名', () => {
    expect(formatKeyShortcut('n', ['command'])).toBe('⌘ N')
    expect(formatKeyShortcut('c', ['command', 'shift'])).toBe('⌘ ⇧ C')
    expect(formatKeyShortcut('enter', ['command'])).toBe('⌘ Return')
  })
})

describe('FIFO 队列纯操作', () => {
  const a = approval({ id: 'a', tool_name: 'computer_type' })
  const b = approval({ id: 'b', tool_name: 'computer_key' })
  const c = approval({ id: 'c', tool_name: 'computer_click' })

  it('pushApproval 追加保序（FIFO）且按 id 去重', () => {
    const q1 = pushApproval([], a)
    const q2 = pushApproval(q1, b)
    const q3 = pushApproval(q2, a) // 去重
    expect(q3.map((item) => item.id)).toEqual(['a', 'b'])
  })

  it('maybePushComputerApproval 忽略普通审批', () => {
    const normal = approval({ id: 'n', tool_name: 'run_shell_command' })
    const q = maybePushComputerApproval([], normal)
    expect(q).toEqual([])
    const withComputer = maybePushComputerApproval(q, a)
    expect(withComputer.map((item) => item.id)).toEqual(['a'])
  })

  it('maybePushDesktopApproval 只入队 desktop；sandbox 忽略', () => {
    const sandbox = approval({ id: 'n', tool_name: 'run_shell_command' })
    const desktop = approval({ id: 'd', tool_name: 'computer_type', ui_scope: 'desktop' })
    const q = maybePushDesktopApproval([], sandbox)
    expect(q).toEqual([])
    const withDesktop = maybePushDesktopApproval(q, desktop)
    expect(withDesktop.map((item) => item.id)).toEqual(['d'])
  })

  it('removeApproval 按 id 移除', () => {
    const q = pushApproval(pushApproval([], a), b)
    const after = removeApproval(q, 'a')
    expect(after.map((item) => item.id)).toEqual(['b'])
    // 不存在也安全
    expect(removeApproval(after, 'zzz')).toEqual(after)
  })

  it('多个审批 → 队首是第一个，队列计数正确', () => {
    const q = [a, b, c]
    expect(q[0].id).toBe('a')
    expect(q.length).toBe(3)
  })
})
