import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'

import {
  createConversation,
  getConversation,
  listConversations,
  sendMessage,
} from '../api/conversations'
import Composer from '../components/Composer'
import ConversationList from '../components/ConversationList'
import MessageList from '../components/MessageList'
import RunActivity from '../components/RunActivity'

export default function ChatPage(): React.JSX.Element {
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [activeRunId, setActiveRunId] = useState<string | null>(null)

  const conversationsQuery = useQuery({
    queryKey: ['conversations'],
    queryFn: () => listConversations(),
  })
  const conversations = conversationsQuery.data ?? []

  // 默认选中最近会话。
  useEffect(() => {
    if (selectedId === null && conversations.length > 0) {
      setSelectedId(conversations[0].id)
    }
  }, [conversations, selectedId])

  const conversationQuery = useQuery({
    queryKey: ['conversation', selectedId],
    queryFn: () => (selectedId ? getConversation(selectedId) : Promise.resolve(null)),
    enabled: selectedId !== null,
  })

  const newConversationMutation = useMutation({
    mutationFn: () => createConversation(),
    onSuccess: (conversation) => {
      setSelectedId(conversation.id)
      void queryClient.invalidateQueries({ queryKey: ['conversations'] })
    },
  })

  const sendMutation = useMutation({
    mutationFn: ({ conversationId, content }: { conversationId: string; content: string }) =>
      sendMessage(conversationId, content),
    onSuccess: (data) => {
      setActiveRunId(data.run.id)
      void queryClient.invalidateQueries({ queryKey: ['conversation', selectedId] })
      void queryClient.invalidateQueries({ queryKey: ['conversations'] })
      void queryClient.invalidateQueries({ queryKey: ['runs'] })
    },
  })

  const messages = conversationQuery.data?.messages ?? []
  const latestAssistant = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      if (messages[i].role === 'assistant') return messages[i].content
    }
    return null
  }, [messages])

  return (
    <div className="page">
      <div style={{ width: 240, borderRight: '1px solid var(--border)', background: 'var(--bg-panel)', display: 'flex', flexDirection: 'column' }}>
        <ConversationList
          conversations={conversations}
          selectedId={selectedId}
          onSelect={(id) => {
            setSelectedId(id)
            setActiveRunId(null)
          }}
          onNew={() => newConversationMutation.mutate()}
        />
      </div>

      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
        <div style={{ flex: 1, overflowY: 'auto', padding: 16 }}>
          {selectedId === null ? (
            <div className="empty">选择或新建一个会话。</div>
          ) : (
            <MessageList messages={messages} />
          )}
        </div>
        {latestAssistant && (
          <div style={{ borderTop: '1px solid var(--border)', padding: '6px 16px', fontSize: 12, color: 'var(--text-dim)' }}>
            最后回答：{latestAssistant.slice(0, 160)}
          </div>
        )}
        <Composer
          disabled={selectedId === null || sendMutation.isPending}
          onSend={async (content) => {
            if (selectedId === null) return
            await sendMutation.mutateAsync({ conversationId: selectedId, content })
          }}
        />
      </div>

      <div style={{ width: 300, borderLeft: '1px solid var(--border)', background: 'var(--bg-panel)', display: 'flex', flexDirection: 'column' }}>
        <RunActivity runId={activeRunId} />
      </div>
    </div>
  )
}
