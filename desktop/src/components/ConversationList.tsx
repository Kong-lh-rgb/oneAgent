import type { Conversation } from '../api/types'
import { Icon } from './Icon'

const STATUS_META: Record<string, { label: string; tone: string }> = {
  running: { label: 'Working', tone: 'running' },
  pending: { label: 'Waiting', tone: 'waiting' },
  completed: { label: 'Completed', tone: 'completed' },
  failed: { label: 'Failed', tone: 'failed' },
  cancelled: { label: 'Cancelled', tone: 'cancelled' },
  interrupted: { label: 'Stopped', tone: 'failed' },
}

function relativeTime(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  const diff = Date.now() - date.getTime()
  const minutes = Math.floor(diff / 60_000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes}m`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h`
  const days = Math.floor(hours / 24)
  return `${days}d`
}

export default function ConversationList({
  conversations,
  selectedId,
  onSelect,
  onNew,
  statusByConversation = {},
}: {
  conversations: Conversation[]
  selectedId: string | null
  onSelect: (id: string) => void
  onNew: () => void
  /** conversationId → 最近 run 状态（Agent workspace 感：状态比消息数更重要）。 */
  statusByConversation?: Record<string, string>
}): React.JSX.Element {
  return (
    <div className="conversation-sidebar__content">
      <div className="conversation-sidebar__top">
        <div className="conversation-sidebar__label">Conversations</div>
        <button className="new-conversation" type="button" onClick={onNew}>
          <Icon name="plus" size={14} />
          New
        </button>
      </div>
      <div className="conversation-list">
        {conversations.length === 0 ? (
          <div className="conversation-list__empty">No conversations yet</div>
        ) : null}
        {conversations.map((conversation) => {
          const status = statusByConversation[conversation.id]
          const meta = status ? STATUS_META[status] : undefined
          return (
            <button
              key={conversation.id}
              type="button"
              className={`conversation-item ${selectedId === conversation.id ? 'active' : ''}`}
              onClick={() => onSelect(conversation.id)}
              aria-current={selectedId === conversation.id ? 'true' : undefined}
            >
              <span className="conversation-item__title">
                {conversation.title || 'Untitled conversation'}
              </span>
              <span className="conversation-item__meta">
                {meta ? (
                  <span
                    className={`conversation-item__status conversation-item__status--${meta.tone}`}
                  >
                    {meta.label}
                  </span>
                ) : null}
                <span>{relativeTime(conversation.updated_at)}</span>
              </span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
