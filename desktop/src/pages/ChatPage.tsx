import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'

import { approveApproval, denyApproval, listApprovals } from '../api/approvals'
import { listArtifacts } from '../api/artifacts'
import {
  createConversation,
  getConversation,
  listConversations,
  sendMessage,
} from '../api/conversations'
import { getTask, planAccept, planReject } from '../api/tasks'
import type { AgentMode, Message, Task } from '../api/types'
import ApprovalCard from '../components/ApprovalCard'
import ChatEmptyState from '../components/ChatEmptyState'
import ChatHeader from '../components/ChatHeader'
import Composer from '../components/Composer'
import ConversationList from '../components/ConversationList'
import LiveAgentTurn from '../components/LiveAgentTurn'
import MessageList from '../components/MessageList'
import PlanCard from '../components/PlanCard'
import ResultCard from '../components/ResultCard'
import RunActivity from '../components/RunActivity'
import { Icon } from '../components/Icon'
import { SectionHeader } from '../components/ui'
import { useEventsStore } from '../stores/events'

export default function ChatPage(): React.JSX.Element {
  const queryClient = useQueryClient()
  const eventsByRun = useEventsStore((state) => state.eventsByRun)
  const runStatuses = useEventsStore((state) => state.runStatuses)
  const streamTextByRun = useEventsStore((state) => state.streamTextByRun)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [lastRunId, setLastRunId] = useState<string | null>(null)
  const [mode, setMode] = useState<AgentMode>('normal')
  const [draft, setDraft] = useState('')
  const [conversationSidebarOpen, setConversationSidebarOpen] = useState(true)
  const [activityOpen, setActivityOpen] = useState(false)
  const [planTask, setPlanTask] = useState<Task | null>(null)
  const [planResolved, setPlanResolved] = useState<string | null>(null)
  const [optimisticMessage, setOptimisticMessage] = useState<{
    conversationId: string
    message: Message
  } | null>(null)
  const [sendError, setSendError] = useState<string | null>(null)
  // LiveAgentTurn 生命周期：流式中 → 收起动画（settling）→ 原位切换为落库消息。
  const [liveTurnActive, setLiveTurnActive] = useState(false)
  const [liveTurnSettling, setLiveTurnSettling] = useState(false)


  const conversationsQuery = useQuery({
    queryKey: ['conversations'],
    queryFn: () => listConversations(),
  })
  const conversations = conversationsQuery.data ?? []

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

  const approvalsQuery = useQuery({
    queryKey: ['chat-approvals', activeRunId],
    queryFn: () => listApprovals('pending'),
    refetchInterval: 2000,
    enabled: activeRunId !== null,
  })
  const pendingApproval =
    approvalsQuery.data?.find((approval) => approval.run_id === activeRunId) ?? null

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
      setSelectedId(conversation.id)
      setLastRunId(null)
      setPlanTask(null)
      setPlanResolved(null)
      setLiveTurnActive(false)
      setLiveTurnSettling(false)
      void queryClient.invalidateQueries({ queryKey: ['conversations'] })
    },
  })

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

  const storedMessages = conversationQuery.data?.messages ?? []
  const messages =
    optimisticMessage?.conversationId === selectedId
      ? [...storedMessages, optimisticMessage.message]
      : storedMessages
  // 流式/收起动画期间，最后一条尚未“正式落库”的 assistant 回复由 LiveAgentTurn
  // 原位展示，避免落库瞬间与流式回复重复、以及回复重排造成的“跳一下”。
  const displayMessages =
    liveTurnActive && messages.at(-1)?.role === 'assistant'
      ? messages.slice(0, -1)
      : messages
  const selectedConversation = conversations.find((item) => item.id === selectedId)
  const showNewConversationHome = selectedId !== null && messages.length === 0
  const progressRunId = sendMutation.isPending ? liveRunId : activeRunId
  const activeEvents = progressRunId ? (eventsByRun[progressRunId] ?? []) : []
  const latestModelStep = [...activeEvents]
    .reverse()
    .find((event) => event.type === 'model_started')?.step
  const streamedText =
    progressRunId && latestModelStep !== null && latestModelStep !== undefined
      ? (streamTextByRun[progressRunId]?.[latestModelStep] ?? '')
      : ''
  // 最近一次 model_completed 携带的模型思考内容（DeepSeek/Qwen reasoning）。
  const latestModelReasoning =
    [...activeEvents]
      .reverse()
      .find((event) => event.type === 'model_completed')?.message?.reasoning ?? ''

  // 流式限速：字符级平滑呈现，便于人眼观看（不快不慢）。
  // 每 tick 只新增固定字符数，追上真实文本后自然等待下一批；
  // 输出完毕/收起时一次性补全，交给落库消息原位切换。
  const STREAM_TICK_MS = 24
  const STREAM_CHARS_PER_TICK = 5
  const [revealedText, setRevealedText] = useState('')
  const revealedCountRef = useRef(0)
  const targetRef = useRef('')
  const textIdentityRef = useRef('')

  // 新 Run / 新 step 开始时重置揭示进度；非流式状态直接显示全部。
  useEffect(() => {
    const identity = `${progressRunId}:${latestModelStep ?? ''}`
    if (textIdentityRef.current !== identity) {
      textIdentityRef.current = identity
      revealedCountRef.current = 0
    }
    targetRef.current = streamedText
    if (!(liveTurnActive && !liveTurnSettling && sendMutation.isPending)) {
      revealedCountRef.current = streamedText.length
      setRevealedText(streamedText)
    }
  }, [
    liveTurnActive,
    liveTurnSettling,
    sendMutation.isPending,
    streamedText,
    progressRunId,
    latestModelStep,
  ])

  // 流式期间开启揭示循环，输出完毕/收起时由上面的 effect 一次性补全。
  useEffect(() => {
    const streaming =
      liveTurnActive && !liveTurnSettling && sendMutation.isPending
    if (!streaming) return undefined
    const interval = window.setInterval(() => {
      const target = targetRef.current
      const next = Math.min(
        target.length,
        revealedCountRef.current + STREAM_CHARS_PER_TICK,
      )
      if (next > revealedCountRef.current) {
        revealedCountRef.current = next
        setRevealedText(target.slice(0, next))
      }
    }, STREAM_TICK_MS)
    return () => window.clearInterval(interval)
  }, [liveTurnActive, liveTurnSettling, sendMutation.isPending])

  const chooseExamplePrompt = (prompt: string): void => {
    setDraft(prompt)
    if (selectedId === null && !newConversationMutation.isPending) {
      newConversationMutation.mutate()
    }
  }

  // Agent 回复（流式文本 / 事件 / 新消息 / 结果出现）时自动滚动到底部。
  // 用聚合 key 而非原始引用，避免轮询 refetch（2s approvals / 3s artifacts）
  // 每次返回新引用导致空闲时反复拽回底部。
  const conversationScrollRef = useRef<HTMLDivElement>(null)
  const autoScrollKey = [
    messages.length,
    messages.at(-1)?.content?.length ?? 0,
    revealedText.length,
    activeEvents.length,
    sendMutation.isPending,
    artifacts.length,
    pendingApproval?.id ?? null,
    planTask?.id ?? null,
    sendError,
    selectedId,
    liveTurnActive,
  ].join('|')
  useEffect(() => {
    const el = conversationScrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [autoScrollKey])

  // 输出完毕（isPending 变 false 且会话数据已刷新）后：先让活动块平滑收起，
  // 再原位切换为正式消息，避免瞬间卸载导致的“跳一下”。
  useEffect(() => {
    if (!liveTurnActive) return
    if (sendMutation.isPending) return
    if (conversationQuery.isLoading || conversationQuery.isFetching) return
    setLiveTurnSettling(true)
    const timer = window.setTimeout(() => {
      setLiveTurnActive(false)
      setLiveTurnSettling(false)
    }, 340)
    return () => window.clearTimeout(timer)
  }, [
    liveTurnActive,
    sendMutation.isPending,
    conversationQuery.isLoading,
    conversationQuery.isFetching,
  ])

  return (
    <div className="chat-workspace">
      <aside
        className={`conversation-sidebar ${conversationSidebarOpen ? 'open' : 'collapsed'}`}
        aria-hidden={!conversationSidebarOpen}
      >
        <ConversationList
          conversations={conversations}
          selectedId={selectedId}
          onSelect={(id) => {
            setSelectedId(id)
            setLastRunId(null)
            setPlanTask(null)
            setPlanResolved(null)
            setActivityOpen(false)
            setLiveTurnActive(false)
            setLiveTurnSettling(false)
          }}
          onNew={() => newConversationMutation.mutate()}
        />
      </aside>

      <main className="conversation-main">
        <ChatHeader
          title={selectedConversation?.title || 'New conversation'}
          conversationSidebarOpen={conversationSidebarOpen}
          onToggleConversationSidebar={() => setConversationSidebarOpen((open) => !open)}
          runStatus={activeRunStatus}
          activityOpen={activityOpen}
          onToggleActivity={() => setActivityOpen((open) => !open)}
        />

        <div ref={conversationScrollRef} className={`conversation-scroll ${showNewConversationHome ? 'conversation-scroll--empty' : ''}`}>
          <div className="message-thread">
            {selectedId === null ? (
              <section className="no-conversation">
                <div className="chat-empty__mark">oa</div>
                <h1>Start a conversation</h1>
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

            {liveTurnActive ? (
              <LiveAgentTurn
                events={activeEvents}
                streamText={revealedText}
                reasoning={latestModelReasoning}
                settling={liveTurnSettling}
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
          mode={mode}
          onModeChange={setMode}
          value={draft}
          onValueChange={setDraft}
          onSend={async (content) => {
            if (!selectedId) return
            setSendError(null)
            setLiveTurnActive(true)
            setLiveTurnSettling(false)
            setOptimisticMessage({
              conversationId: selectedId,
              message: { role: 'user', content },
            })
            await sendMutation.mutateAsync({
              conversationId: selectedId,
              content,
              sendMode: mode,
            })
          }}
        />
      </main>

      {activityOpen ? (
        <div className="activity-drawer">
          <RunActivity runId={activeRunId} onClose={() => setActivityOpen(false)} />
        </div>
      ) : null}
    </div>
  )
}
