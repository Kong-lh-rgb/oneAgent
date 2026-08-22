/** CommandPalette：搜索与键盘导航的纯交互逻辑测试。 */

import { describe, expect, it, vi } from 'vitest'

import {
  filterCommands,
  nextEnabledCommandIndex,
  type ComposerCommand,
} from './CommandPalette'

function commands(): ComposerCommand[] {
  return [
    { id: 'new', label: 'New conversation', hint: '⌘N', onSelect: vi.fn() },
    { id: 'plan', label: 'Plan mode', disabled: true, onSelect: vi.fn() },
    { id: 'runs', label: 'Open runs', hint: 'Execution history', onSelect: vi.fn() },
  ]
}

describe('CommandPalette logic', () => {
  it('按标签和 hint 搜索，空查询返回全部命令', () => {
    expect(filterCommands(commands(), '')).toHaveLength(3)
    expect(filterCommands(commands(), 'NEW').map((item) => item.id)).toEqual(['new'])
    expect(filterCommands(commands(), 'history').map((item) => item.id)).toEqual(['runs'])
  })

  it('方向键导航循环并跳过 disabled 命令', () => {
    const items = commands()
    expect(nextEnabledCommandIndex(items, 0, 1)).toBe(2)
    expect(nextEnabledCommandIndex(items, 2, 1)).toBe(0)
    expect(nextEnabledCommandIndex(items, 0, -1)).toBe(2)
  })

  it('空结果保持安全索引', () => {
    expect(nextEnabledCommandIndex([], 0, 1)).toBe(0)
  })
})
