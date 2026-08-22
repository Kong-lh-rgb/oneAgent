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
  it('无 Run 时显示就绪与标题', () => {
    const html = renderToStaticMarkup(<RunStatusBar {...base} />)
    expect(html).toContain('就绪')
    expect(html).toContain('打开备忘录输入你好')
    expect(html).toContain('详情')
  })

  it('运行中使用中文字段展示阶段、步骤、操作、用量和耗时', () => {
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
    expect(html).toContain('正在执行')
    expect(html).toContain('第 4 步')
    expect(html).toContain('3 次操作')
    expect(html).toContain('用量 8.4k Token')
    expect(html).toContain('7.2s')
    expect(html).toContain('Typing in Notes')
    expect(html).toContain('>停止</button>')
  })

  it('完成时显示已完成且无停止按钮', () => {
    const html = renderToStaticMarkup(
      <RunStatusBar
        {...base}
        runStatus="completed"
        step={10}
        toolCount={8}
        totalTokens={21400}
      />,
    )
    expect(html).toContain('已完成')
    expect(html).toContain('10')
    expect(html).not.toContain('>停止</button>')
  })

  it('失败显示中文原因和统计', () => {
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
    expect(html).toContain('已停止')
    expect(html).toContain('已达到最大执行步数')
    expect(html).toContain('10 步')
    expect(html).toContain('8 次操作')
    expect(html).toContain('用量 24.1k Token')
    expect(html).toContain('详情')
  })

  it('中断后提供恢复动作', () => {
    const html = renderToStaticMarkup(
      <RunStatusBar {...base} runStatus="interrupted" onRecover={() => {}} />,
    )
    expect(html).toContain('已中断')
    expect(html).toContain('恢复')
  })

  it('Plan 模式显示中文规划标识', () => {
    const html = renderToStaticMarkup(
      <RunStatusBar {...base} mode="plan" runStatus="running" />,
    )
    expect(html).toContain('规划模式')
  })
})
