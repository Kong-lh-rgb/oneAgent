import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { approveApproval, denyApproval, listApprovals } from '../api/approvals'
import { listArtifacts } from '../api/artifacts'
import {
  createConversation,
  deleteConversation,
  getConversation,
  listConversations,
  renameConversation,
  sendMessage,
} from '../api/conversations'
import { cancelRun, interruptRun, listRuns, recoverRun } from '../api/runs'
import { getTask, planAccept, planReject } from '../api/tasks'
import type { AgentMode, Message, Task } from '../api/types'
import { latestRunId } from '../agent/runAnalysis'
import { buildTurnView } from '../agent/turnPresentation'
import { chatShouldShowApproval } from '../approval/computerApproval'
import ApprovalCard from '../components/ApprovalCard'
import ChatEmptyState from '../components/ChatEmptyState'
import RunStatusBar from '../components/RunStatusBar'
import Composer from '../components/Composer'
import type { ComposerCommand } from '../components/Composer'
import ConversationList from '../components/ConversationList'
import LiveAgentTurn from '../components/LiveAgentTurn'
import MessageList from '../components/MessageList'
import PlanCard from '../components/PlanCard'
import ResultCard from '../components/ResultCard'
import RunActivity from '../components/RunActivity'
import { Icon } from '../components/Icon'
import { SectionHeader } from '../components/ui'
import { useEventsStore } from '../stores/events'
import type { PageKey } from '../App'

export default function ChatPage({
  onNavigate,
  onOpenRun,
  initialConversationId,
  onConversationChange,
}: {
  onNavigate?: (page: PageKey) => void
  onOpenRun?: (runId: string) => void
  initialConversationId?: string | null
  onConversationChange?: (conversationId: string | null) => void
}): React.JSX.Element {
  const queryClient = useQueryClient()
  const eventsByRun = useEventsStore((state) => state.eventsByRun)
  const runStatuses = useEventsStore((state) => state.runStatuses)
  const connected = useEventsStore((state) => state.connected)
  const [selectedId, setSelectedId] = useState<string | null>(
    initialConversationId ?? null,
  )
  const [lastRunId, setLastRunId] = useState<string | null>(null)
  const [mode, setMode] = useState<AgentMode>('normal')
  const [draft, setDraft] = useState('')
  const [conversationSidebarOpen, setConversationSidebarOpen] = useState(true)
  const [runInspectorOpen, setRunInspectorOpen] = useState(false)
  const [planTask, setPlanTask] = useState<Task | null>(null)
  const [planResolved, setPlanResolved] = useState<string | null>(null)
  const [optimisticMessage, setOptimisticMessage] = useState<{
    conversationId: string
    message: Message
  } | null>(null)
  const [sendError, setSendError] = useState<string | null>(null)
  // Persistent AgentTurn：完成后仍保留为本轮 Work Record。
  const [liveTurnActive, setLiveTurnActive] = useState(false)

  const selectConversation = useCallback((conversationId: string | null): void => {
    setSelectedId(conversationId)
    onConversationChange?.(conversationId)
  }, [onConversationChange])

  const conversationsQuery = useQuery({
    queryKey: ['conversations'],
    queryFn: () => listConversations(),
  })
  const conversations = conversationsQuery.data ?? []

  useEffect(() => {
    if (selectedId === null && conversations.length > 0) {
      selectConversation(conversations[0].id)
    }
  }, [conversations, selectedId, selectConversation])

  useEffect(() => {
    if (initialConversationId && initialConversationId !== selectedId) {
      setSelectedId(initialConversationId)
    }
  }, [initialConversationId, selectedId])

  const conversationQuery = useQuery({
    queryKey: ['conversation', selectedId],
    queryFn: () => (selectedId ? getConversation(selectedId) : Promise.resolve(null)),
    enabled: selectedId !== null,
  })

  // conversation.send 返回前也能从共享事件流识别当前 Run，不需要改 RPC。
  const liveRunId = useMemo(() => {
    if (!selectedId) return null
    let candidate: { id: string; time: string } | null = null
    for (const [runId, events] of Object.entries(eventsByRun)) {
      const latest = events.at(-1)
      if (!latest || latest.conversation_id !== selectedId) continue
      if (!['pending', 'running'].includes(runStatuses[runId] ?? '')) continue
      if (!candidate || latest.event_time > candidate.time) {
        candidate = { id: runId, time: latest.event_time }
      }
    }
    return candidate?.id ?? null
  }, [eventsByRun, runStatuses, selectedId])
  const activeRunId = liveRunId ?? lastRunId
  const activeRunStatus = activeRunId ? runStatuses[activeRunId] : undefined
  const isRunning = Boolean(activeRunId) &&
    (activeRunStatus === 'running' || activeRunStatus === 'pending')

  // 实时 Run 一旦出现就记住 id；终态通知可能早于 conversation.send 返回，
  // 不能因 running → completed 让 Persistent AgentTurn 短暂丢失事件。
  useEffect(() => {
    if (liveRunId) setLastRunId(liveRunId)
  }, [liveRunId])

  // 后端 SQLite 是权威：会话切换/挂载/断线重连（后端重启）时同步该会话 runs 的
  // 真实状态，避免历史 run 因错过实时事件而长期停留在 running/pending（暂停
  // 按钮、LiveAgentTurn 卡住的根源）；重连后同时刷新会话数据。
  useEffect(() => {
    if (!selectedId) return
    let cancelled = false
    void listRuns({ conversationId: selectedId, limit: 50 })
      .then((runs) => {
        if (cancelled) return
        const map: Record<string, string> = {}
        for (const run of runs) map[run.id] = run.status
        useEventsStore.getState().syncRunStatuses(map)
        const persistedRunId = latestRunId(runs)
        setLastRunId((current) => current ?? persistedRunId)
      })
      .catch(() => {
        /* 同步失败不影响 UI；实时事件仍会工作 */
      })
    if (connected) {
      void queryClient.invalidateQueries({ queryKey: ['conversation', selectedId] })
      void queryClient.invalidateQueries({ queryKey: ['conversations'] })
      void queryClient.invalidateQueries({ queryKey: ['runs'] })
    }
    return () => {
      cancelled = true
    }
  }, [selectedId, connected, queryClient])

  // conversation → 最近 run 状态（让会话列表呈现 Agent workspace 状态）。
  const { conversationStatus, conversationActivity } = useMemo(() => {
    const map: Record<string, string> = {}
    const activity: Record<string, string> = {}
    const latestTimes: Record<string, string> = {}
    for (const [runId, events] of Object.entries(eventsByRun)) {
      const latest = events.at(-1)
      const conv = latest?.conversation_id
      if (!conv) continue
      if (latestTimes[conv] && latestTimes[conv] > latest.event_time) continue
      latestTimes[conv] = latest.event_time
      const status = runStatuses[runId]
      if (status) map[conv] = status
      const turn = buildTurnView(events, { now: Date.now() })
      if (turn.currentAction) activity[conv] = turn.currentAction
      else if (turn.targetApp) activity[conv] = turn.targetApp
    }
    return { conversationStatus: map, conversationActivity: activity }
  }, [eventsByRun, runStatuses])

  const approvalsQuery = useQuery({
    queryKey: ['chat-approvals', activeRunId],
    queryFn: () => listApprovals('pending'),
    refetchInterval: 2000,
    enabled: activeRunId !== null,
  })
  // Chat 只负责 sandbox 审批；desktop 审批始终归独立浮窗。
  const pendingApproval =
    approvalsQuery.data?.find(
      (approval) => chatShouldShowApproval(approval, activeRunId),
    ) ?? null

  const artifactsQuery = useQuery({
    queryKey: ['chat-artifacts', activeRunId],
    queryFn: () =>
      activeRunId ? listArtifacts({ runId: activeRunId }) : Promise.resolve([]),
    refetchInterval: 3000,
    enabled: activeRunId !== null,
  })
  const artifacts = artifactsQuery.data ?? []

  const resolveApprovalMutation = useMutation({
    mutationFn: (action: { id: string; decision: 'approve' | 'deny' }) =>
      action.decision === 'approve'
        ? approveApproval(action.id)
        : denyApproval(action.id),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ['chat-approvals'] })
      void queryClient.invalidateQueries({ queryKey: ['approvals'] })
    },
  })

  const newConversationMutation = useMutation({
    mutationFn: () => createConversation(),
    onSuccess: (conversation) => {
      selectConversation(conversation.id)
      setLastRunId(null)
      setPlanTask(null)
      setPlanResolved(null)
      setLiveTurnActive(false)
      void queryClient.invalidateQueries({ queryKey: ['conversations'] })
    },
  })

  /** 重命名会话（接入后端）。 */
  const renameConversationAction = async (id: string, title: string): Promise<void> => {
    try {
      await renameConversation(id, title)
      void queryClient.invalidateQueries({ queryKey: ['conversations'] })
      void queryClient.invalidateQueries({ queryKey: ['conversation', id] })
    } catch (error) {
      setSendError(error instanceof Error ? error.message : String(error))
    }
  }

  /** 删除会话（接入后端）；删除当前会话时切到下一个。 */
  const deleteConversationAction = async (id: string): Promise<void> => {
    try {
      await deleteConversation(id)
      if (id === selectedId) {
        const next = conversations.find((c) => c.id !== id)?.id ?? null
        selectConversation(next)
        setLastRunId(null)
        setPlanTask(null)
        setPlanResolved(null)
        setLiveTurnActive(false)
        setRunInspectorOpen(false)
      }
      void queryClient.invalidateQueries({ queryKey: ['conversations'] })
      void queryClient.invalidateQueries({ queryKey: ['conversation', id] })
    } catch (error) {
      setSendError(error instanceof Error ? error.message : String(error))
    }
  }

  const sendMutation = useMutation({
    mutationFn: ({
      conversationId,
      content,
      sendMode,
    }: {
      conversationId: string
      content: string
      sendMode: AgentMode
    }) => sendMessage(conversationId, content, sendMode),
    onSuccess: async (data) => {
      setLastRunId(data.run.id)
      setPlanResolved(null)
      if (data.run.mode === 'plan' && data.plan_task_id) {
        try {
          setPlanTask(await getTask(data.plan_task_id))
        } catch {
          setPlanTask(null)
        }
      } else {
        setPlanTask(null)
      }
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['conversation', selectedId] }),
        queryClient.invalidateQueries({ queryKey: ['conversations'] }),
        queryClient.invalidateQueries({ queryKey: ['runs'] }),
        queryClient.invalidateQueries({ queryKey: ['chat-artifacts'] }),
      ])
    },
    onError: (error: unknown) => {
      setSendError(error instanceof Error ? error.message : String(error))
    },
    onSettled: () => setOptimisticMessage(null),
  })

  // 断线重连兜底：若发送请求在断线期间未 settle（异常路径，正常断线 rpcClient
  // 会 reject 挂起请求），连接恢复时重置，避免 Composer 输入框被 sending 锁死。
  const prevConnectedRef = useRef(connected)
  useEffect(() => {
    const reconnected = !prevConnectedRef.current && connected
    prevConnectedRef.current = connected
    if (reconnected && sendMutation.isPending) {
      setOptimisticMessage(null)
      sendMutation.reset()
    }
  }, [connected, sendMutation])

  const resolvePlanMutation = useMutation({
    mutationFn: (action: { taskId: string; decision: 'accept' | 'reject' }) =>
      action.decision === 'accept'
        ? planAccept(action.taskId)
        : planReject(action.taskId),
    onSuccess: (_task, variables) => {
      setPlanResolved(variables.decision === 'accept' ? 'Plan accepted' : 'Plan rejected')
      setPlanTask(null)
      void queryClient.invalidateQueries({ queryKey: ['tasks'] })
    },
    onError: (error: unknown) => {
      setPlanResolved(error instanceof Error ? error.message : String(error))
    },
  })

  const stopRun = async (): Promise<void> => {
    if (!activeRunId) return
    try {
      const updated = await cancelRun(activeRunId)
      // 即使 run.status 广播错过，也立即用 RPC 响应里的权威状态覆盖 store。
      useEventsStore
        .getState()
        .syncRunStatuses({ [activeRunId]: updated.status })
      void queryClient.invalidateQueries({ queryKey: ['runs'] })
      void queryClient.invalidateQueries({ queryKey: ['conversation', selectedId] })
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      const stateMatch = /cannot cancel run in state (\w+)/.exec(message)
      if (activeRunId && stateMatch) {
        // run 已进入终态（状态广播延迟导致暂停按钮仍可点）：修正本地状态，
        // 这是用户点暂停的正常收敛路径，不当作错误提示。
        useEventsStore.getState().syncRunStatuses({ [activeRunId]: stateMatch[1] })
        void queryClient.invalidateQueries({ queryKey: ['runs'] })
        void queryClient.invalidateQueries({ queryKey: ['conversation', selectedId] })
      } else {
        setSendError(message)
      }
    }
  }

  const recoverRunAction = async (): Promise<void> => {
    if (!activeRunId) return
    try {
      const result = await recoverRun(activeRunId)
      setLastRunId(result.run.id)
      void queryClient.invalidateQueries({ queryKey: ['runs'] })
      void queryClient.invalidateQueries({ queryKey: ['conversation', selectedId] })
    } catch (error) {
      setSendError(error instanceof Error ? error.message : String(error))
    }
  }

  /** 暂停（中断）Run：保留 Checkpoint，可从断点继续（Recover）。 */
  const pauseRun = async (): Promise<void> => {
    if (!activeRunId) return
    try {
      const updated = await interruptRun(activeRunId)
      // 即使 run.status 广播错过，也立即用 RPC 响应里的权威状态覆盖 store。
      useEventsStore
        .getState()
        .syncRunStatuses({ [activeRunId]: updated.status })
      void queryClient.invalidateQueries({ queryKey: ['runs'] })
      void queryClient.invalidateQueries({ queryKey: ['conversation', selectedId] })
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      const stateMatch = /cannot interrupt run in state (\w+)/.exec(message)
      if (activeRunId && stateMatch) {
        // run 已进入终态（状态广播延迟）：修正本地状态，不当作错误提示。
        useEventsStore.getState().syncRunStatuses({ [activeRunId]: stateMatch[1] })
        void queryClient.invalidateQueries({ queryKey: ['runs'] })
        void queryClient.invalidateQueries({ queryKey: ['conversation', selectedId] })
      } else {
        setSendError(message)
      }
    }
  }

  // Command palette（⌘K）：轻量能力入口，不做永久按钮墙。
  const composerCommands: ComposerCommand[] = [
    { id: 'new', label: 'New conversation', icon: 'plus', onSelect: () => newConversationMutation.mutate() },
    {
      id: 'plan',
      label: mode === 'plan' ? 'Switch to Normal mode' : 'Switch to Plan mode',
      icon: 'check',
      onSelect: () => setMode((m) => (m === 'plan' ? 'normal' : 'plan')),
    },
    { id: 'computer', label: 'Open Computer', icon: 'computer', onSelect: () => onNavigate?.('computer') },
    {
      id: 'runs',
      label: 'View current Run',
      icon: 'runs',
      onSelect: () => {
        if (activeRunId) onOpenRun?.(activeRunId)
      },
    },
    { id: 'stop', label: 'Stop Run', icon: 'close', onSelect: () => void stopRun() },
    { id: 'artifacts', label: 'View artifacts', icon: 'artifacts', onSelect: () => onNavigate?.('artifacts') },
    { id: 'settings', label: 'Settings', icon: 'settings', onSelect: () => onNavigate?.('settings') },
  ]

  const storedMessages = conversationQuery.data?.messages ?? []
  const messages =
    optimisticMessage?.conversationId === selectedId
      ? [...storedMessages, optimisticMessage.message]
      : storedMessages
  const showAgentTurn = liveTurnActive || liveRunId !== null
  // Persistent AgentTurn 已承载最新 assistant reply，避免与落库消息重复。
  const displayMessages =
    showAgentTurn && messages.at(-1)?.role === 'assistant'
      ? messages.slice(0, -1)
      : messages
  const selectedConversation = conversations.find((item) => item.id === selectedId)
  const showNewConversationHome = selectedId !== null && messages.length === 0
  const progressRunId = activeRunId
  const activeEvents = progressRunId ? (eventsByRun[progressRunId] ?? []) : []
  const latestModelStep = [...activeEvents]
    .reverse()
    .find((event) => event.type === 'model_started')?.step
  // 流式正文/思考由 LiveAgentTurn 内部按 run+step 细粒度订阅，ChatPage 不参与高频渲染。

  // Run Status Bar 数据：当前最重要的执行状态 + 统计。
  const turnView = buildTurnView(activeEvents, { now: Date.now() })
  const currentAction = turnView.currentAction
  const startedEvent = activeEvents.find((event) => event.type === 'agent_started')
  const startedAt = startedEvent ? Date.parse(startedEvent.event_time) : null
  const failedEvent = [...activeEvents]
    .reverse()
    .find((event) => event.type === 'agent_failed')
  const stopReason = failedEvent?.stop_reason ?? null

  // 流式揭示：已删除二次打字机（revealedText / setInterval / STREAM_TICK_MS / CHARS_PER_TICK）。
  // Provider delta 由 events store 短时 batching（~33ms flush）批量提交，
  // LiveAgentTurn 内部按 run+step 细粒度订阅，直接渲染最新文本；complete 时 store 立即 flush。

  const chooseExamplePrompt = (prompt: string): void => {
    setDraft(prompt)
    if (selectedId === null && !newConversationMutation.isPending) {
      newConversationMutation.mutate()
    }
  }

  // stick-to-bottom：用户靠近底部时自动跟随，向上滚动后停止强制拉底；
  // 滚动更新用 rAF 合并，避免每个字符增量都强制同步布局。
  const conversationScrollRef = useRef<HTMLDivElement>(null)
  const stickToBottomRef = useRef(true)
  const scrollFrameRef = useRef<number | null>(null)

  const handleConversationScroll = (): void => {
    const el = conversationScrollRef.current
    if (!el) return
    stickToBottomRef.current =
      el.scrollHeight - el.scrollTop - el.clientHeight < 120
  }

  const scheduleScroll = (): void => {
    if (scrollFrameRef.current !== null) return
    scrollFrameRef.current = requestAnimationFrame(() => {
      scrollFrameRef.current = null
      const el = conversationScrollRef.current
      if (el && stickToBottomRef.current) el.scrollTop = el.scrollHeight
    })
  }

  // 用聚合 key 而非原始引用，避免轮询 refetch（2s approvals / 3s artifacts）
  // 每次返回新引用导致空闲时反复拽回底部。
  const autoScrollKey = [
    messages.length,
    messages.at(-1)?.content?.length ?? 0,
    activeEvents.length,
    sendMutation.isPending,
    artifacts.length,
    pendingApproval?.id ?? null,
    planTask?.id ?? null,
    sendError,
    selectedId,
    showAgentTurn,
  ].join('|')
  useEffect(() => {
    scheduleScroll()
    return () => {
      if (scrollFrameRef.current !== null) {
        cancelAnimationFrame(scrollFrameRef.current)
        scrollFrameRef.current = null
      }
    }
  }, [autoScrollKey])

  return (
    <div className="chat-workspace">
      <aside
        className={`conversation-sidebar ${conversationSidebarOpen ? 'open' : 'collapsed'}`}
        aria-hidden={!conversationSidebarOpen}
      >
        <ConversationList
          conversations={conversations}
          selectedId={selectedId}
          statusByConversation={conversationStatus}
          activityByConversation={conversationActivity}
          onSelect={(id) => {
            selectConversation(id)
            setLastRunId(null)
            setPlanTask(null)
            setPlanResolved(null)
            setRunInspectorOpen(false)
            setLiveTurnActive(false)
          }}
          onNew={() => newConversationMutation.mutate()}
          onRename={(id, title) => void renameConversationAction(id, title)}
          onDelete={(id) => void deleteConversationAction(id)}
        />
      </aside>

      <div className="chat-right">
        <RunStatusBar
          title={selectedConversation?.title || 'New conversation'}
          conversationSidebarOpen={conversationSidebarOpen}
          onToggleConversationSidebar={() => setConversationSidebarOpen((open) => !open)}
          runStatus={activeRunStatus}
          step={latestModelStep ?? turnView.steps}
          toolCount={turnView.toolCount}
          totalTokens={turnView.usage?.totalTokens ?? null}
          durationMs={turnView.durationMs}
          startedAt={startedAt}
          currentAction={currentAction}
          stopReason={stopReason}
          mode={mode}
          turnState={activeEvents.length > 0 ? turnView.status : undefined}
          activityOpen={runInspectorOpen}
          onToggleActivity={() => setRunInspectorOpen((open) => !open)}
          onStop={() => void stopRun()}
          onRecover={() => void recoverRunAction()}
        />
        <div className="chat-right__body">
          <main className="conversation-main">
            <div
              ref={conversationScrollRef}
          onScroll={handleConversationScroll}
          className={`conversation-scroll ${showNewConversationHome ? 'conversation-scroll--empty' : ''}`}
        >
          <div className="message-thread">
            {selectedId === null ? (
              <section className="no-conversation">
                <div className="chat-empty__mark">V</div>
                <h1>Start new work</h1>
                <p>Create a conversation, then give Vesta something to work on.</p>
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={() => newConversationMutation.mutate()}
                  disabled={newConversationMutation.isPending}
                >
                  <Icon name="plus" size={15} /> New conversation
                </button>
              </section>
            ) : showNewConversationHome ? (
              <ChatEmptyState onSelectPrompt={chooseExamplePrompt} />
            ) : (
              <MessageList messages={displayMessages} />
            )}

            {showAgentTurn ? (
              <LiveAgentTurn
                runId={progressRunId}
                step={latestModelStep ?? null}
                events={activeEvents}
                onRecover={() => void recoverRunAction()}
                onInspect={() => setRunInspectorOpen(true)}
              />
            ) : null}

            {sendError ? (
              <div className="inline-notice inline-notice--error">{sendError}</div>
            ) : null}

            {planTask ? (
              <PlanCard
                task={planTask}
                busy={resolvePlanMutation.isPending}
                onAccept={(taskId) =>
                  resolvePlanMutation.mutate({ taskId, decision: 'accept' })
                }
                onReject={(taskId) =>
                  resolvePlanMutation.mutate({ taskId, decision: 'reject' })
                }
              />
            ) : null}
            {planResolved ? <div className="inline-notice">{planResolved}</div> : null}

            {pendingApproval ? (
              <ApprovalCard
                approval={pendingApproval}
                busy={resolveApprovalMutation.isPending}
                onApprove={(id) => resolveApprovalMutation.mutate({ id, decision: 'approve' })}
                onDeny={(id) => resolveApprovalMutation.mutate({ id, decision: 'deny' })}
              />
            ) : null}

            {artifacts.length > 0 ? (
              <section className="results-section">
                <SectionHeader title="Results" hint={`${artifacts.length} delivered`} />
                <div className="results-list">
                  {artifacts.map((artifact) => (
                    <ResultCard key={artifact.id} artifact={artifact} />
                  ))}
                </div>
              </section>
            ) : null}
          </div>
        </div>

        <Composer
          disabled={selectedId === null}
          sending={sendMutation.isPending}
          running={isRunning}
          onStop={() => void pauseRun()}
          mode={mode}
          onModeChange={setMode}
          value={draft}
          onValueChange={setDraft}
          commands={composerCommands}
          onSend={async (content) => {
            if (!selectedId) return
            setSendError(null)
            setLastRunId(null)
            setLiveTurnActive(true)
            setOptimisticMessage({
              conversationId: selectedId,
              message: { role: 'user', content },
            })
            // 发送即定位到底部：避免先停留在上一条回复的位置，再等 live turn 出现。
            requestAnimationFrame(() => {
              const el = conversationScrollRef.current
              if (el) el.scrollTop = el.scrollHeight
            })
            await sendMutation.mutateAsync({
              conversationId: selectedId,
              content,
              sendMode: mode,
            })
          }}
          />
          </main>
          {runInspectorOpen ? (
            <div className="chat-panels">
              <div className="activity-drawer">
                <RunActivity
                  runId={activeRunId}
                  onClose={() => setRunInspectorOpen(false)}
                  onStop={() => void stopRun()}
                  onRecover={() => void recoverRunAction()}
                  onOpenFullDetail={onOpenRun}
                />
              </div>
            </div>
          ) : null}
      </div>
      </div>
    </div>
  )
}
