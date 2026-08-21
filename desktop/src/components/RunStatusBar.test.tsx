/** RunStatusBar：Agent Command Workspace 顶部 Run 状态条测试。 */

import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import RunStatusBar from './RunStatusBar'

const base = {
  title: '打开备忘录输入你好',
  conversationSidebarOpen: true,
  onToggleConversationSidebar: () => {},
  activityOpen: false,
  onToggleActivity: () => {},
}

describe('RunStatusBar', () => {
  it('无 Run 时显示 Idle 与标题', () => {
    const html = renderToStaticMarkup(<RunStatusBar {...base} />)
    expect(html).toContain('Idle')
    expect(html).toContain('打开备忘录输入你好')
    expect(html).toContain('Activity')
  })

  it('运行中显示 Working、Step/tools/tokens/duration 与 Stop', () => {
    const html = renderToStaticMarkup(
      <RunStatusBar
        {...base}
        runStatus="running"
        step={4}
        toolCount={3}
        totalTokens={8400}
        durationMs={7200}
        currentAction="Typing in Notes"
      />,
    )
    expect(html).toContain('Working')
    expect(html).toContain('Step 4')
    expect(html).toContain('3 tools')
    expect(html).toContain('8.4k tokens')
    expect(html).toContain('7.2s')
    expect(html).toContain('Typing in Notes')
    expect(html).toContain('>Stop</button>')
  })

  it('完成时显示 Completed 且无 Stop', () => {
    const html = renderToStaticMarkup(
      <RunStatusBar
        {...base}
        runStatus="completed"
        step={10}
        toolCount={8}
        totalTokens={21400}
      />,
    )
    expect(html).toContain('Completed')
    expect(html).toContain('10')
    expect(html).not.toContain('>Stop</button>')
  })

  it('失败显示 Stopped + reason + 统计 + Activity', () => {
    const html = renderToStaticMarkup(
      <RunStatusBar
        {...base}
        runStatus="failed"
        step={10}
        toolCount={8}
        totalTokens={24100}
        stopReason="max_steps"
      />,
    )
    expect(html).toContain('Stopped')
    expect(html).toContain('Maximum step limit reached')
    expect(html).toContain('10 steps')
    expect(html).toContain('8 tools')
    expect(html).toContain('24.1k tokens')
    expect(html).toContain('Activity')
  })

  it('interrupted 提供 Recover 动作', () => {
    const html = renderToStaticMarkup(
      <RunStatusBar {...base} runStatus="interrupted" onRecover={() => {}} />,
    )
    expect(html).toContain('Stopped')
    expect(html).toContain('Recover')
  })

  it('Plan 模式显示 Plan 标识', () => {
    const html = renderToStaticMarkup(
      <RunStatusBar {...base} mode="plan" runStatus="running" />,
    )
    expect(html).toContain('Plan')
  })
})
