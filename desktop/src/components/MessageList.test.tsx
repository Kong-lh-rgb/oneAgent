/** MessageList：空状态 + 用户/助手消息渲染测试。 */

import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import type { Message } from '../api/types'
import MessageList from './MessageList'

const userMsg: Message = { role: 'user', content: '帮我总结仓库' }
const assistantMsg: Message = {
  role: 'assistant',
  content: '## 结论\n这是 **重点** 和 `code`。\n\n- 第一项\n1. 第二项\n```ts\nconst a = 1\n```',
}

describe('MessageList', () => {
  it('空状态提示', () => {
    const html = renderToStaticMarkup(<MessageList messages={[]} />)
    expect(html).toContain('开始对话')
    expect(html).toContain('empty-state')
  })

  it('用户消息使用 message-user 表面', () => {
    const html = renderToStaticMarkup(<MessageList messages={[userMsg]} />)
    expect(html).toContain('message-user')
    expect(html).toContain('帮我总结仓库')
  })

  it('助手消息平铺渲染，不做大气泡', () => {
    const html = renderToStaticMarkup(<MessageList messages={[assistantMsg]} />)
    expect(html).toContain('message-assistant')
    expect(html).not.toContain('message-assistant__body" style=')
    // markdown：粗体 / 行内代码 / 代码块
    expect(html).toContain('<h3>结论</h3>')
    expect(html).toContain('<strong>重点</strong>')
    expect(html).toContain('<code>code</code>')
    expect(html).toContain('<pre>')
    expect(html.match(/message-assistant__list-item/g)).toHaveLength(2)
  })

  it('过滤 system 消息', () => {
    const html = renderToStaticMarkup(
      <MessageList
        messages={[
          { role: 'system', content: '你是助手' },
          userMsg,
          assistantMsg,
        ]}
      />,
    )
    expect(html).not.toContain('你是助手')
    expect(html).toContain('帮我总结仓库')
  })
})
