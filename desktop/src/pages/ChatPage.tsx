import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'

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

  const chooseExamplePrompt = (prompt: string): void => {
    setDraft(prompt)
    if (selectedId === null && !newConversationMutation.isPending) {
      newConversationMutation.mutate()
    }
  }

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

        <div className={`conversation-scroll ${showNewConversationHome ? 'conversation-scroll--empty' : ''}`}>
          <div className="message-thread">
            {selectedId === null ? (
              <section className="no-conversation">
                <div className="chat-empty__mark">oa</div>
                <h1>Start a conversation</h1>
                <p>Create a conversation, then give oneAgent something to work on.</p>
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
              <MessageList messages={messages} />
            )}

            {sendMutation.isPending ? (
              <LiveAgentTurn events={activeEvents} streamText={streamedText} />
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
