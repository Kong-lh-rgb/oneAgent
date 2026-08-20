import type { ReactElement } from 'react'

import { Icon } from './Icon'
import { StatusDot, type StatusTone } from './ui'

const RUN_STATUS: Record<string, { label: string; tone: StatusTone }> = {
  pending: { label: 'Waiting', tone: 'waiting' },
  running: { label: 'Working', tone: 'running' },
  completed: { label: 'Completed', tone: 'completed' },
  failed: { label: 'Failed', tone: 'failed' },
  cancelled: { label: 'Cancelled', tone: 'offline' },
  interrupted: { label: 'Interrupted', tone: 'waiting' },
}

export default function ChatHeader({
  title,
  conversationSidebarOpen,
  onToggleConversationSidebar,
  runStatus,
  activityOpen,
  onToggleActivity,
}: {
  title: string
  conversationSidebarOpen: boolean
  onToggleConversationSidebar: () => void
  runStatus?: string
  activityOpen: boolean
  onToggleActivity: () => void
}): ReactElement {
  const status = runStatus ? RUN_STATUS[runStatus] : undefined
  return (
    <header className="chat-header">
      <button
        type="button"
        className="icon-btn"
        onClick={onToggleConversationSidebar}
        aria-label={conversationSidebarOpen ? '收起会话列表' : '展开会话列表'}
        title={conversationSidebarOpen ? 'Hide conversations' : 'Show conversations'}
      >
        <Icon name={conversationSidebarOpen ? 'panelClose' : 'panelOpen'} />
      </button>
      <div className="chat-header__title">{title}</div>
      <div className="chat-header__actions">
        {status ? (
          <span className="run-status">
            <StatusDot tone={status.tone} />
            {status.label}
          </span>
        ) : null}
        <button
          type="button"
          className={`header-action ${activityOpen ? 'active' : ''}`}
          onClick={onToggleActivity}
          aria-pressed={activityOpen}
        >
          <Icon name="activity" size={15} />
          Activity
        </button>
      </div>
    </header>
  )
}
