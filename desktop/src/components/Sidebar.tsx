/** 64px Desktop Nav Rail：图标导航 + 底部 Settings / Host 状态。 */

import type { ReactElement } from 'react'
import type { PageKey } from '../App'
import { Icon } from './Icon'
import type { IconName } from './Icon'
import { StatusDot } from './ui'

export interface SidebarItem {
  key: PageKey
  label: string
  icon: IconName
}

export const SIDEBAR_ITEMS: SidebarItem[] = [
  { key: 'chat', label: 'Chat', icon: 'chat' },
  { key: 'runs', label: 'Runs', icon: 'runs' },
  { key: 'automations', label: 'Automations', icon: 'automations' },
  { key: 'approvals', label: 'Approvals', icon: 'approvals' },
  { key: 'artifacts', label: 'Artifacts', icon: 'artifacts' },
  { key: 'computer', label: 'Computer', icon: 'computer' },
]

export interface SidebarProps {
  current: PageKey
  onNavigate: (page: PageKey) => void
  connected: boolean
}

export default function Sidebar({
  current,
  onNavigate,
  connected,
}: SidebarProps): ReactElement {
  return (
    <nav className="sidebar" aria-label="主导航">
      <div className="sidebar-brand" aria-label="Vesta">oa</div>
      <div className="sidebar-nav">
        {SIDEBAR_ITEMS.map((item) => (
          <button
            key={item.key}
            type="button"
            className={`nav-item ${current === item.key ? 'active' : ''}`}
            onClick={() => onNavigate(item.key)}
            aria-current={current === item.key ? 'page' : undefined}
            aria-label={item.label}
            title={item.label}
          >
            <Icon name={item.icon} />
            <span className="nav-item__label">{item.label}</span>
          </button>
        ))}
      </div>
      <div className="sidebar-footer">
        <button
          type="button"
          className={`nav-item ${current === 'settings' ? 'active' : ''}`}
          onClick={() => onNavigate('settings')}
          aria-current={current === 'settings' ? 'page' : undefined}
          title="Settings"
          aria-label="Settings"
        >
          <Icon name="settings" />
          <span className="nav-item__label">Settings</span>
        </button>
        <div
          className="host-status"
          title={connected ? 'Host connected' : 'Host offline'}
          aria-label={connected ? 'Host Ready' : 'Host Offline'}
        >
          <StatusDot tone={connected ? 'ready' : 'offline'} />
        </div>
      </div>
    </nav>
  )
}
