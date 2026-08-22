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
  it('渲染新建入口、标题、状态与选中态', () => {
    const html = renderToStaticMarkup(
      <ConversationList
        conversations={[conversation]}
        selectedId="conv-1"
        statusByConversation={{ 'conv-1': 'running' }}
        onSelect={() => {}}
        onNew={() => {}}
      />,
    )
    expect(html).toContain('New')
    expect(html).toContain('Desktop redesign')
    expect(html).toContain('Working')
    expect(html).toContain('conversation-item__status--running')
    expect(html).toContain('conversation-item active')
    expect(html).not.toContain('12 messages')
  })

  it('空列表提供轻量空状态', () => {
    const html = renderToStaticMarkup(
      <ConversationList conversations={[]} selectedId={null} onSelect={() => {}} onNew={() => {}} />,
    )
    expect(html).toContain('No conversations yet')
  })

  it('突出 working / approval / failed，弱化 completed，并展示当前动作', () => {
    const conversations = ['running', 'pending', 'failed', 'completed'].map((status) => ({
      ...conversation,
      id: status,
      title: status,
    }))
    const html = renderToStaticMarkup(
      <ConversationList
        conversations={conversations}
        selectedId="running"
        statusByConversation={{ running: 'running', pending: 'pending', failed: 'failed', completed: 'completed' }}
        activityByConversation={{ running: 'Typing in Notes', pending: 'Waiting for approval' }}
        onSelect={() => {}}
        onNew={() => {}}
      />,
    )
    expect(html).toContain('Typing in Notes')
    expect(html).toContain('Waiting for approval')
    expect(html).toContain('Working')
    expect(html).toContain('Waiting')
    expect(html).toContain('Failed')
    expect(html).toContain('conversation-item__status--completed">✓')
    expect(html).not.toContain('>Completed</span>')
  })
})
