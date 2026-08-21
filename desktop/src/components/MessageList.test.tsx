/** MessageList：空状态 + 用户/助手消息渲染测试。 */

import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import type { Message } from '../api/types'
import MessageList from './MessageList'

const userMsg: Message = { role: 'user', content: '帮我总结仓库' }
const reasoningMsg: Message = {
  role: 'assistant',
  content: '结论',
  reasoning: '先拆解需求，再核对仓库文件',
}
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
    // markdown：标题 / 粗体 / 行内代码 / 列表 / 代码块
    expect(html).toContain('<h2>结论</h2>')
    expect(html).toContain('<strong>重点</strong>')
    expect(html).toContain('<code>code</code>')
    expect(html).toContain('<ul>')
    expect(html).toContain('<ol>')
    expect(html).toContain('<pre')
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

  it('助手消息带 reasoning 时展示思考过程', () => {
    const html = renderToStaticMarkup(<MessageList messages={[reasoningMsg]} />)
    expect(html).toContain('思考过程')
    expect(html).toContain('先拆解需求，再核对仓库文件')
    expect(html).toContain('assistant-reasoning')
  })

  it('连续 assistant 消息合并成一条回复，只显示一个头像', () => {
    const html = renderToStaticMarkup(
      <MessageList
        messages={[
          { role: 'user', content: '帮我做' },
          { role: 'assistant', content: '' },
          { role: 'assistant', content: '第一段回复' },
          { role: 'assistant', content: '第二段回复' },
        ]}
      />,
    )
    expect(html).toContain('第一段回复')
    expect(html).toContain('第二段回复')
    expect(html).toContain('message-assistant--continuation')
    // 空正文的中间 tool-call 消息被跳过，头像只出现一次
    expect(html.match(/message-assistant__avatar/g)).toHaveLength(1)
  })

  it('带工具调用的 assistant 消息一律不渲染（去掉长串工具调用）', () => {
    const html = renderToStaticMarkup(
      <MessageList
        messages={[
          { role: 'user', content: '帮我做' },
          {
            role: 'assistant',
            content: '让我先看一下',
            tool_calls: [
              { id: 'call-1', name: 'computer_observe', arguments: {} },
            ],
          },
          {
            role: 'assistant',
            content: '让我操作一下',
            tool_calls: [
              { id: 'call-2', name: 'computer_click', arguments: {} },
            ],
          },
          { role: 'assistant', content: '完成，这是结果。' },
        ]}
      />,
    )
    expect(html).not.toContain('让我先看一下')
    expect(html).not.toContain('让我操作一下')
    expect(html).not.toContain('computer_observe')
    expect(html).toContain('完成，这是结果。')
    // 只有最终一条正文，只有一个头像
    expect(html.match(/message-assistant__avatar/g)).toHaveLength(1)
  })
})
