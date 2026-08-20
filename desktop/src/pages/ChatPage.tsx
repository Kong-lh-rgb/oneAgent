import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'

import {
  createConversation,
  getConversation,
  listConversations,
  sendMessage,
} from '../api/conversations'
import { getTask, planAccept, planReject } from '../api/tasks'
import type { AgentMode, Task } from '../api/types'
import Composer from '../components/Composer'
import ConversationList from '../components/ConversationList'
import MessageList from '../components/MessageList'
import RunActivity from '../components/RunActivity'

export default function ChatPage(): React.JSX.Element {
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [activeRunId, setActiveRunId] = useState<string | null>(null)
  const [mode, setMode] = useState<AgentMode>('normal')
  // Plan Mode 结果：生成的 PENDING Task + 展示/操作状态。
  const [planTask, setPlanTask] = useState<Task | null>(null)
  const [planResolved, setPlanResolved] = useState<string | null>(null)

  const conversationsQuery = useQuery({
    queryKey: ['conversations'],
    queryFn: () => listConversations(),
  })
  const conversations = conversationsQuery.data ?? []

  // 默认选中最近会话。
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

  const newConversationMutation = useMutation({
    mutationFn: () => createConversation(),
    onSuccess: (conversation) => {
      setSelectedId(conversation.id)
      setPlanTask(null)
      void queryClient.invalidateQueries({ queryKey: ['conversations'] })
    },
  })

  const sendMutation = useMutation({
    mutationFn: ({
      conversationId,
      content,
      mode: sendMode,
    }: {
      conversationId: string
      content: string
      mode: AgentMode
    }) => sendMessage(conversationId, content, sendMode),
    onSuccess: async (data) => {
      setActiveRunId(data.run.id)
      setPlanResolved(null)
      // Plan Mode：若生成了 PENDING Task，拉取详情用于展示 + Accept/Reject。
      if (data.run.mode === 'plan' && data.plan_task_id) {
        try {
          setPlanTask(await getTask(data.plan_task_id))
        } catch {
          setPlanTask(null)
        }
      } else {
        setPlanTask(null)
      }
      void queryClient.invalidateQueries({ queryKey: ['conversation', selectedId] })
      void queryClient.invalidateQueries({ queryKey: ['conversations'] })
      void queryClient.invalidateQueries({ queryKey: ['runs'] })
    },
  })

  const resolvePlanMutation = useMutation({
    mutationFn: (action: { taskId: string; decision: 'accept' | 'reject' }) => {
      if (action.decision === 'accept') return planAccept(action.taskId)
      return planReject(action.taskId)
    },
    onSuccess: (_task, variables) => {
      setPlanResolved(variables.decision === 'accept' ? 'accepted' : 'rejected')
      setPlanTask(null)
      void queryClient.invalidateQueries({ queryKey: ['tasks'] })
    },
    onError: (err: unknown) => {
      setPlanResolved(err instanceof Error ? err.message : String(err))
    },
  })

  const messages = conversationQuery.data?.messages ?? []
  const latestAssistant = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      if (messages[i].role === 'assistant') return messages[i].content
    }
    return null
  }, [messages])

  return (
    <div className="page">
      <div style={{ width: 240, borderRight: '1px solid var(--border)', background: 'var(--bg-panel)', display: 'flex', flexDirection: 'column' }}>
        <ConversationList
          conversations={conversations}
          selectedId={selectedId}
          onSelect={(id) => {
            setSelectedId(id)
            setActiveRunId(null)
            setPlanTask(null)
            setPlanResolved(null)
          }}
          onNew={() => newConversationMutation.mutate()}
        />
      </div>

      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
        <div style={{ flex: 1, overflowY: 'auto', padding: 16 }}>
          {selectedId === null ? (
            <div className="empty">选择或新建一个会话。</div>
          ) : (
            <MessageList messages={messages} />
          )}

          {/* Plan Mode 结果：生成的计划 + Accept / Reject */}
          {planTask && (
            <div className="approval-card" style={{ marginTop: 12 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <strong>{planTask.title}</strong>
                <span className="badge badge-pending">{planTask.status}</span>
              </div>
              {planTask.goal && (
                <div className="text-dim" style={{ marginTop: 4 }}>
                  goal: {planTask.goal}
                </div>
              )}
              <ol style={{ margin: '8px 0 0', paddingLeft: 18 }}>
                {planTask.steps.map((step, index) => (
                  <li key={step.id ?? index} style={{ marginBottom: 2 }}>
                    {step.title}
                  </li>
                ))}
              </ol>
              <div style={{ display: 'flex', gap: 6, marginTop: 10 }}>
                <button
                  className="btn btn-primary btn-sm"
                  disabled={resolvePlanMutation.isPending}
                  onClick={() =>
                    resolvePlanMutation.mutate({
                      taskId: planTask.id,
                      decision: 'accept',
                    })
                  }
                >
                  Accept Plan
                </button>
                <button
                  className="btn btn-danger btn-sm"
                  disabled={resolvePlanMutation.isPending}
                  onClick={() =>
                    resolvePlanMutation.mutate({
                      taskId: planTask.id,
                      decision: 'reject',
                    })
                  }
                >
                  Reject
                </button>
              </div>
            </div>
          )}
          {planResolved && (
            <div className="text-dim" style={{ marginTop: 8 }}>
              {planResolved === 'accepted'
                ? 'Plan accepted'
                : planResolved === 'rejected'
                  ? 'Plan rejected'
                  : planResolved}
            </div>
          )}
        </div>
        {latestAssistant && (
          <div style={{ borderTop: '1px solid var(--border)', padding: '6px 16px', fontSize: 12, color: 'var(--text-dim)' }}>
            最后回答：{latestAssistant.slice(0, 160)}
          </div>
        )}
        <Composer
          disabled={selectedId === null || sendMutation.isPending}
          onSend={async (content) => {
            if (selectedId === null) return
            await sendMutation.mutateAsync({
              conversationId: selectedId,
              content,
              mode,
            })
          }}
        />
        {/* 简单模式选择：Normal / Plan */}
        <div
          style={{
            display: 'flex',
            gap: 6,
            padding: '6px 16px 8px',
            borderTop: '1px solid var(--border)',
          }}
        >
          {(['normal', 'plan'] as const).map((item) => (
            <button
              key={item}
              className={`btn btn-sm ${mode === item ? 'btn-primary' : ''}`}
              onClick={() => setMode(item)}
            >
              {item === 'normal' ? 'Normal' : 'Plan'}
            </button>
          ))}
          <span
            className="text-dim"
            style={{ marginLeft: 8, fontSize: 12, alignSelf: 'center' }}
          >
            {mode === 'plan' ? 'Plan Mode：只读调查 + 生成计划' : 'Normal Mode'}
          </span>
        </div>
      </div>

      <div style={{ width: 300, borderLeft: '1px solid var(--border)', background: 'var(--bg-panel)', display: 'flex', flexDirection: 'column' }}>
        <RunActivity runId={activeRunId} />
      </div>
    </div>
  )
}
