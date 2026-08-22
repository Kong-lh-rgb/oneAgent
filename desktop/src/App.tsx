import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { listApprovals } from './api/approvals'
import { listArtifacts } from './api/artifacts'
import { getComputerStatus } from './api/computer'
import { useEventsStore } from './stores/events'
import { createDesktopNotificationController } from './notifications/desktop'
import Sidebar from './components/Sidebar'
import { ToastViewport } from './components/ToastViewport'
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
  const [everConnected, setEverConnected] = useState(false)

  const connect = useEventsStore((state) => state.connect)
  const disconnect = useEventsStore((state) => state.disconnect)
  const connected = useEventsStore((state) => state.connected)
  const runStatuses = useEventsStore((state) => state.runStatuses)

  useEffect(() => {
    connect()
    return () => disconnect()
  }, [connect, disconnect])

  useEffect(() => {
    if (connected) setEverConnected(true)
  }, [connected])

  useEffect(() => {
    const controller = createDesktopNotificationController()
    controller?.start()
    return () => controller?.stop()
  }, [])

  const pendingApprovalsQuery = useQuery({
    queryKey: ['sidebar-approvals'],
    queryFn: () => listApprovals('pending'),
    refetchInterval: 4000,
  })

  // 侧栏背景状态：最近有新交付物（very light，不刷 dashboard 数字墙）。
  const artifactsIndicatorQuery = useQuery({
    queryKey: ['rail-artifacts'],
    queryFn: () => listArtifacts({ limit: 1 }),
    refetchInterval: 6000,
  })
  const hasArtifacts = (artifactsIndicatorQuery.data?.length ?? 0) > 0
  const computerIndicatorQuery = useQuery({
    queryKey: ['computer-status'],
    queryFn: getComputerStatus,
    refetchInterval: 3000,
    retry: false,
  })
  const computerActive = Boolean(computerIndicatorQuery.data?.lease?.busy)

  // 实时 running run 数（来自 run.status 事件）+ pending 审批数 → 侧栏徽标。
  const runningCount = Object.values(runStatuses).filter(
    (status) => status === 'running',
  ).length
  const pendingApprovalCount = pendingApprovalsQuery.data?.length ?? 0

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
        badges={{
          runs: runningCount,
          approvals: pendingApprovalCount,
        }}
        dots={{
          chat: runningCount > 0,
          artifacts: hasArtifacts,
          computer: computerActive,
        }}
      />
      <div className="main">
        {!connected && everConnected ? (
          <div className="host-banner" role="status">
            <span className="host-banner__dot" />
            Host 连接已断开，正在重连…
          </div>
        ) : null}
        {page === 'chat' && <ChatPage onNavigate={navigate} />}
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
      <ToastViewport />
    </div>
  )
}
