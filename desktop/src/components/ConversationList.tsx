import type { Conversation } from '../api/types'
import { Icon } from './Icon'

export default function ConversationList({
  conversations,
  selectedId,
  onSelect,
  onNew,
}: {
  conversations: Conversation[]
  selectedId: string | null
  onSelect: (id: string) => void
  onNew: () => void
}): React.JSX.Element {
  return (
    <div className="conversation-sidebar__content">
      <div className="conversation-sidebar__top">
        <div className="conversation-sidebar__label">Conversations</div>
        <button className="new-conversation" type="button" onClick={onNew}>
          <Icon name="plus" size={14} />
          New conversation
        </button>
      </div>
      <div className="conversation-list">
        {conversations.length === 0 ? (
          <div className="conversation-list__empty">No conversations yet</div>
        ) : null}
        {conversations.map((conversation) => (
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
              {conversation.message_count} messages
            </span>
          </button>
        ))}
      </div>
    </div>
  )
}
