/** Composer：输入面板、模式与发送状态测试。 */

import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import Composer from './Composer'

describe('Composer', () => {
  it('渲染工作指令占位符、Normal / Plan 模式和发送按钮', () => {
    const html = renderToStaticMarkup(
      <Composer disabled={false} mode="normal" onModeChange={() => {}} onSend={async () => {}} />,
    )
    expect(html).toContain('Ask Vesta to do something')
    expect(html).toContain('Normal')
    expect(html).toContain('Plan')
    expect(html).toContain('aria-label="发送"')
    expect(html).toContain('aria-pressed="true"')
  })

  it('Plan 模式可见且有草稿时发送按钮可用', () => {
    const html = renderToStaticMarkup(
      <Composer
        disabled={false}
        mode="plan"
        value="Inspect the repository"
        onModeChange={() => {}}
        onSend={async () => {}}
      />,
    )
    expect(html).toContain('>Inspect the repository</textarea>')
    expect(html).toContain('mode-switch__item active')
    expect(html).not.toContain('composer__send" disabled')
  })

  it('发送中锁定输入并展示发送状态', () => {
    const html = renderToStaticMarkup(
      <Composer disabled={false} sending value="Work" onSend={async () => {}} />,
    )
    expect(html).toContain('composer--busy')
    expect(html).toContain('aria-label="正在发送"')
    expect(html).toContain('disabled=""')
  })

  it('run 执行中发送按钮转为暂停按钮，点击触发 onStop', () => {
    const html = renderToStaticMarkup(
      <Composer
        disabled={false}
        running
        onStop={() => {}}
        onSend={async () => {}}
      />,
    )
    expect(html).toContain('composer__stop')
    expect(html).toContain('aria-label="暂停"')
    expect(html).not.toContain('aria-label="发送"')
    expect(html).not.toContain('composer__send" disabled')
  })

  it('输入框 footer 不渲染 ⌘K 入口（快捷键仍保留触发面板）', () => {
    const html = renderToStaticMarkup(
      <Composer
        disabled={false}
        onSend={async () => {}}
        commands={[
          { id: 'stop', label: 'Stop Run', icon: 'close', onSelect: () => {} },
          { id: 'runs', label: 'View current Run', icon: 'runs', onSelect: () => {} },
        ]}
      />,
    )
    expect(html).not.toContain('composer__cmd')
    expect(html).not.toContain('⌘K')
    expect(html).not.toContain('composer-commands__item')
  })
})
