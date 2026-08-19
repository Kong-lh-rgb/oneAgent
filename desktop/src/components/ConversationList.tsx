import type { Conversation } from '../api/types'

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
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ padding: 8 }}>
        <button className="btn btn-primary" style={{ width: '100%' }} onClick={onNew}>
          ＋ 新建会话
        </button>
      </div>
      <div style={{ flex: 1, overflowY: 'auto' }}>
        {conversations.length === 0 && (
          <div className="empty">暂无会话</div>
        )}
        {conversations.map((conversation) => (
          <button
            key={conversation.id}
            className="nav-item"
            style={{
              display: 'block',
              width: '100%',
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              ...(selectedId === conversation.id
                ? { background: 'var(--accent-soft)', color: 'var(--accent)' }
                : {}),
            }}
            onClick={() => onSelect(conversation.id)}
          >
            {conversation.title || '未命名会话'}
            <span className="text-muted" style={{ marginLeft: 6, fontSize: 12 }}>
              {conversation.message_count}
            </span>
          </button>
        ))}
      </div>
    </div>
  )
}
