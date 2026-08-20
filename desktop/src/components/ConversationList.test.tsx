/** ConversationList：紧凑列表、选中态与新建入口测试。 */

import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import type { Conversation } from '../api/types'
import ConversationList from './ConversationList'

const conversation: Conversation = {
  id: 'conv-1',
  title: 'Desktop redesign',
  message_count: 12,
  created_at: '2026-08-20T00:00:00+00:00',
  updated_at: '2026-08-20T01:00:00+00:00',
}

describe('ConversationList', () => {
  it('渲染新建入口、会话标题、消息数与选中态', () => {
    const html = renderToStaticMarkup(
      <ConversationList
        conversations={[conversation]}
        selectedId="conv-1"
        onSelect={() => {}}
        onNew={() => {}}
      />,
    )
    expect(html).toContain('New conversation')
    expect(html).toContain('Desktop redesign')
    expect(html).toContain('12 messages')
    expect(html).toContain('conversation-item active')
  })

  it('空列表提供轻量空状态', () => {
    const html = renderToStaticMarkup(
      <ConversationList conversations={[]} selectedId={null} onSelect={() => {}} onNew={() => {}} />,
    )
    expect(html).toContain('No conversations yet')
  })
})
