/** 审批路由与展示的纯逻辑（无 React、无副作用，便于单元测试）。

路由依据：
- 声明式 ``ui_scope``：desktop → 始终进入浮窗；sandbox → 始终进入 Chat。
- 一条审批从出现到解决只归属一个 surface，焦点变化不能迁移审批。
后端是权威来源；``ui_scope`` 缺失时（旧数据）回退 tool_name 前缀。
*/

import type { ApprovalRequest } from '../api/types'

/**
 * Desktop 审批：作用于用户真实桌面，始终由独立浮窗处理。
 * 优先用后端声明的 ui_scope；缺失时（旧数据）回退 computer_ 前缀。
 */
export function isDesktopApproval(approval: ApprovalRequest): boolean {
  if (approval.ui_scope) return approval.ui_scope === 'desktop'
  return approval.tool_name.startsWith('computer_')
}

/** 兼容旧名：Computer Approval == Desktop Approval。 */
export function isComputerApproval(approval: ApprovalRequest): boolean {
  return isDesktopApproval(approval)
}

/** Sandbox 审批：作用于 Vesta 沙盒 / 宿主，永远进 Chat。 */
export function isSandboxApproval(approval: ApprovalRequest): boolean {
  return !isDesktopApproval(approval)
}

/** Chat 内是否展示：普通（sandbox）审批 + 属于当前 active Run。 */
export function isChatApproval(
  approval: ApprovalRequest,
  activeRunId: string | null,
): boolean {
  return isSandboxApproval(approval) && approval.run_id === activeRunId
}

/** Chat 路由：只有当前 Run 的 sandbox 审批进入 Chat。 */
export function chatShouldShowApproval(
  approval: ApprovalRequest,
  activeRunId: string | null,
): boolean {
  return isSandboxApproval(approval) && approval.run_id === activeRunId
}

/** 浮窗路由：所有 desktop 审批始终进入浮窗。 */
export function floatingShouldShowApproval(approval: ApprovalRequest): boolean {
  return isDesktopApproval(approval)
}

// ---------------------------------------------------------------------------
// 面向普通用户的动作文案（不要直接把 tool_name 丢给用户）
// ---------------------------------------------------------------------------

const ACTION_LABELS: Record<string, string> = {
  computer_click: 'Click an interface element',
  computer_type: 'Type text',
  computer_key: 'Press a key or shortcut',
  computer_scroll: 'Scroll the current window',
  computer_open_app: 'Open an application',
  computer_focus_window: 'Focus a window',
}

const ACTION_DESCRIPTIONS: Record<string, string> = {
  computer_click: 'Vesta wants to click an interface element.',
  computer_type: 'Vesta wants to type text into the current application.',
  computer_key: 'Vesta wants to press a key or shortcut.',
  computer_scroll: 'Vesta wants to scroll the current window.',
  computer_open_app: 'Vesta wants to open an application.',
  computer_focus_window: 'Vesta wants to focus a window.',
}

/** 主要动作名（如 “Type text”）；未知 computer_* → “Control this Mac”。 */
export function computerActionLabel(approval: ApprovalRequest): string {
  return ACTION_LABELS[approval.tool_name] ?? 'Control this Mac'
}

/** 描述句（如 “Vesta wants to type text into the current application.”）。 */
export function computerActionDescription(approval: ApprovalRequest): string {
  return (
    ACTION_DESCRIPTIONS[approval.tool_name] ??
    'Vesta wants to control this Mac.'
  )
}

// ---------------------------------------------------------------------------
// Arguments 摘要（主区域只放用户能看懂的内容，原始 JSON 进 Show details）
// ---------------------------------------------------------------------------

const MODIFIER_SYMBOLS: Record<string, string> = {
  command: '⌘',
  cmd: '⌘',
  shift: '⇧',
  option: '⌥',
  alt: '⌥',
  control: '⌃',
  ctrl: '⌃',
}

const NAMED_KEYS: Record<string, string> = {
  enter: 'Return',
  return: 'Return',
  tab: 'Tab',
  escape: 'Esc',
  space: 'Space',
  backspace: '⌫',
  delete: '⌫',
  up: '↑',
  down: '↓',
  left: '←',
  right: '→',
}

function formatKeyName(key: string): string {
  const named = NAMED_KEYS[key]
  if (named) return named
  if (key.length === 1) return key.toUpperCase()
  return key
}

/** 把 key + modifiers 格式化成人类可读快捷键，如 ``⌘ N``。 */
export function formatKeyShortcut(
  key: string,
  modifiers: readonly string[],
): string {
  const symbols = modifiers
    .map((modifier) => MODIFIER_SYMBOLS[modifier] ?? '')
    .filter(Boolean)
  return [...symbols, formatKeyName(key)].join(' ')
}

/** 面向用户的动作参数摘要；无可展示内容返回 null。 */
export function computerActionSummary(approval: ApprovalRequest): string | null {
  const args = approval.arguments ?? {}
  switch (approval.tool_name) {
    case 'computer_type': {
      const text = typeof args.text === 'string' ? args.text.trim() : ''
      return text ? `“${text}”` : null
    }
    case 'computer_key': {
      const key = typeof args.key === 'string' ? args.key : ''
      const modifiers = Array.isArray(args.modifiers)
        ? args.modifiers.filter(
            (item: unknown): item is string => typeof item === 'string',
          )
        : []
      if (!key) return null
      return formatKeyShortcut(key, modifiers)
    }
    case 'computer_click': {
      if (typeof args.element_ref === 'string' && args.element_ref) {
        return `Element ${args.element_ref}`
      }
      if (
        typeof args.x === 'number' &&
        typeof args.y === 'number' &&
        !Number.isNaN(args.x) &&
        !Number.isNaN(args.y)
      ) {
        return `At position (${args.x}, ${args.y})`
      }
      return null
    }
    case 'computer_open_app': {
      return typeof args.app === 'string' && args.app.trim()
        ? args.app.trim()
        : null
    }
    case 'computer_focus_window': {
      return typeof args.window_ref === 'string' && args.window_ref.trim()
        ? args.window_ref.trim()
        : null
    }
    case 'computer_scroll': {
      const dx = typeof args.delta_x === 'number' ? args.delta_x : 0
      const dy = typeof args.delta_y === 'number' ? args.delta_y : 0
      if (dy !== 0) return dy > 0 ? 'Scroll down' : 'Scroll up'
      if (dx !== 0) return dx > 0 ? 'Scroll right' : 'Scroll left'
      return null
    }
    default:
      return null
  }
}

// ---------------------------------------------------------------------------
// FIFO 审批队列纯操作（Floating Window 的 UI queue projection）
// ---------------------------------------------------------------------------

/** 入队：按 id 去重，追加到队尾（FIFO）。 */
export function pushApproval(
  queue: ApprovalRequest[],
  approval: ApprovalRequest,
): ApprovalRequest[] {
  return queue.some((item) => item.id === approval.id)
    ? queue
    : [...queue, approval]
}

/** 只入队 desktop 审批；sandbox 审批直接忽略（浮窗不处理）。 */
export function maybePushDesktopApproval(
  queue: ApprovalRequest[],
  approval: ApprovalRequest,
): ApprovalRequest[] {
  return isDesktopApproval(approval) ? pushApproval(queue, approval) : queue
}

/** 兼容旧名。 */
export function maybePushComputerApproval(
  queue: ApprovalRequest[],
  approval: ApprovalRequest,
): ApprovalRequest[] {
  return maybePushDesktopApproval(queue, approval)
}

/** 按 id 出队（approval.resolved / 本地 resolve 都走这里）。 */
export function removeApproval(
  queue: ApprovalRequest[],
  id: string,
): ApprovalRequest[] {
  return queue.filter((item) => item.id !== id)
}
