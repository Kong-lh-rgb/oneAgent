import { useEffect, useState } from 'react'

import { useEventsStore } from './stores/events'
import { createDesktopNotificationController } from './notifications/desktop'
import ApprovalsPage from './pages/ApprovalsPage'
import ArtifactsPage from './pages/ArtifactsPage'
import AutomationsPage from './pages/AutomationsPage'
import ChatPage from './pages/ChatPage'
import ComputerPage from './pages/ComputerPage'
import RunDetailPage from './pages/RunDetailPage'
import RunsPage from './pages/RunsPage'
import SettingsPage from './pages/SettingsPage'

export type PageKey =
  | 'chat'
  | 'runs'
  | 'automations'
  | 'approvals'
  | 'artifacts'
  | 'computer'
  | 'settings'

export interface AppState {
  page: PageKey
  selectedRunId: string | null
  navigate: (page: PageKey) => void
  openRun: (runId: string) => void
}

export default function App(): React.JSX.Element {
  const [page, setPage] = useState<PageKey>('chat')
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)

  const connect = useEventsStore((state) => state.connect)
  const disconnect = useEventsStore((state) => state.disconnect)

  useEffect(() => {
    connect()
    return () => disconnect()
  }, [connect, disconnect])

  useEffect(() => {
    const controller = createDesktopNotificationController()
    controller?.start()
    return () => controller?.stop()
  }, [])

  const navigate = (next: PageKey): void => {
    setPage(next)
    if (next !== 'runs') setSelectedRunId(null)
  }

  const openRun = (runId: string): void => {
    setSelectedRunId(runId)
    setPage('runs')
  }

  return (
    <div className="app-shell">
      <Sidebar current={page} onNavigate={navigate} />
      <div className="main">
        {page === 'chat' && <ChatPage />}
        {page === 'runs' &&
          (selectedRunId ? (
            <RunDetailPage runId={selectedRunId} onBack={() => setSelectedRunId(null)} />
          ) : (
            <RunsPage openRun={openRun} />
          ))}
        {page === 'automations' && <AutomationsPage />}
        {page === 'approvals' && <ApprovalsPage />}
        {page === 'artifacts' && <ArtifactsPage />}
        {page === 'computer' && <ComputerPage />}
        {page === 'settings' && <SettingsPage />}
      </div>
    </div>
  )
}

function Sidebar({
  current,
  onNavigate,
}: {
  current: PageKey
  onNavigate: (page: PageKey) => void
}): React.JSX.Element {
  const connected = useEventsStore((state) => state.connected)
  const items: Array<{ key: PageKey; label: string }> = [
    { key: 'chat', label: 'Chat' },
    { key: 'runs', label: 'Runs' },
    { key: 'automations', label: 'Automations' },
    { key: 'approvals', label: 'Approvals' },
    { key: 'artifacts', label: 'Artifacts' },
    { key: 'computer', label: 'Computer' },
    { key: 'settings', label: 'Settings' },
  ]
  return (
    <nav className="sidebar">
      <div className="sidebar-brand">OneAgent</div>
      <div className="sidebar-nav">
        {items.map((item) => (
          <button
            key={item.key}
            className={`nav-item ${current === item.key ? 'active' : ''}`}
            onClick={() => onNavigate(item.key)}
          >
            {item.label}
          </button>
        ))}
      </div>
      <div className="sidebar-footer">
        <div>
          Server:{' '}
          <span className={connected ? 'text-dim' : 'error-text'}>
            {connected ? 'connected' : 'offline'}
          </span>
        </div>
        <div className="text-muted">v0.1.0 · Desktop V0</div>
      </div>
    </nav>
  )
}
