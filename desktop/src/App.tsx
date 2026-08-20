import { useEffect, useState } from 'react'

import { useEventsStore } from './stores/events'
import { createDesktopNotificationController } from './notifications/desktop'
import Sidebar from './components/Sidebar'
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
  const connected = useEventsStore((state) => state.connected)

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
      <Sidebar
        current={page}
        onNavigate={navigate}
        connected={connected}
      />
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
