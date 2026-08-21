import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import {
  cancelAutomation,
  createAutomation,
  listAutomations,
  pauseAutomation,
  resumeAutomation,
  type CreateAutomationInput,
} from '../api/automations'
import AutomationForm from '../components/AutomationForm'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { EmptyState, ErrorState, LoadingState } from '../components/PageStates'
import { PageShell } from '../components/PageShell'
import { toast } from '../stores/toasts'

function scheduleText(schedule: {
  kind: string
  run_at: string | null
  interval_seconds: number | null
  cron_expr: string | null
  timezone: string
}): string {
  if (schedule.kind === 'once') return `once · ${schedule.run_at ?? '-'}`
  if (schedule.kind === 'interval') return `interval · ${schedule.interval_seconds}s`
  return `cron · ${schedule.cron_expr ?? '-'}`
}

function formatTime(iso: string | null): string {
  if (!iso) return '-'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleString()
}

export default function AutomationsPage(): React.JSX.Element {
  const queryClient = useQueryClient()
  const [showForm, setShowForm] = useState(false)
  const [cancelTarget, setCancelTarget] = useState<string | null>(null)

  const automationsQuery = useQuery({
    queryKey: ['automations'],
    queryFn: () => listAutomations(),
    refetchInterval: 4000,
  })
  const automations = automationsQuery.data ?? []

  const invalidate = (): void => {
    void queryClient.invalidateQueries({ queryKey: ['automations'] })
    void queryClient.invalidateQueries({ queryKey: ['runs'] })
  }

  const createMutation = useMutation({
    mutationFn: (input: CreateAutomationInput) => createAutomation(input),
    onSuccess: () => {
      setShowForm(false)
      toast.success('Automation 已创建')
      invalidate()
    },
    onError: (err: unknown) => {
      toast.error(err instanceof Error ? err.message : String(err))
    },
  })

  const controlMutation = useMutation({
    mutationFn: (action: { id: string; op: 'pause' | 'resume' | 'cancel' }) => {
      if (action.op === 'pause') return pauseAutomation(action.id)
      if (action.op === 'resume') return resumeAutomation(action.id)
      return cancelAutomation(action.id)
    },
    onSuccess: (_data, action) => {
      const verb =
        action.op === 'pause' ? '已暂停' : action.op === 'resume' ? '已恢复' : '已取消'
      toast.info(`${verb} Automation`)
      invalidate()
    },
    onError: (err: unknown) => {
      toast.error(err instanceof Error ? err.message : String(err))
    },
  })

  return (
    <PageShell
      title="Automations"
      subtitle="定时 / 周期触发 Agent 运行。"
      actions={
        <button
          className="btn btn-primary btn-sm"
          onClick={() => setShowForm((value) => !value)}
        >
          {showForm ? '收起' : '＋ 新建'}
        </button>
      }
    >
      {showForm && (
        <AutomationForm
          onSubmit={async (input) => {
            await createMutation.mutateAsync(input)
          }}
          onCancel={() => setShowForm(false)}
        />
      )}

      {automationsQuery.isPending ? (
        <LoadingState label="正在加载 Automations…" />
      ) : automationsQuery.isError ? (
        <ErrorState
          message={String(automationsQuery.error)}
          onRetry={() => void automationsQuery.refetch()}
        />
      ) : automations.length === 0 ? (
        <EmptyState
          title="暂无 Automation"
          hint="点击右上角「＋ 新建」创建第一个定时任务。"
          icon="automations"
        />
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>title</th>
              <th>status</th>
              <th>schedule</th>
              <th>next_run_at</th>
              <th>last_run_at</th>
              <th>last_run_id</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {automations.map((automation) => (
              <tr key={automation.id}>
                <td>{automation.title}</td>
                <td>
                  <span className={`badge badge-${automation.status}`}>{automation.status}</span>
                </td>
                <td className="text-dim">{scheduleText(automation.schedule)}</td>
                <td className="text-dim">{formatTime(automation.next_run_at)}</td>
                <td className="text-dim">{formatTime(automation.last_run_at)}</td>
                <td className="text-dim">{automation.last_run_id?.slice(0, 8) ?? '-'}</td>
                <td>
                  <div style={{ display: 'flex', gap: 6 }}>
                    <button
                      className="btn btn-sm"
                      disabled={automation.status !== 'active'}
                      onClick={() => controlMutation.mutate({ id: automation.id, op: 'pause' })}
                    >
                      pause
                    </button>
                    <button
                      className="btn btn-sm"
                      disabled={automation.status !== 'paused'}
                      onClick={() => controlMutation.mutate({ id: automation.id, op: 'resume' })}
                    >
                      resume
                    </button>
                    <button
                      className="btn btn-sm btn-danger"
                      disabled={automation.status === 'cancelled' || automation.status === 'completed'}
                      onClick={() => setCancelTarget(automation.id)}
                    >
                      cancel
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <ConfirmDialog
        open={cancelTarget !== null}
        title="取消这个 Automation？"
        message="取消后该定时任务将不再触发，且无法恢复。"
        confirmLabel="取消 Automation"
        busy={controlMutation.isPending}
        onConfirm={() => {
          if (cancelTarget) {
            controlMutation.mutate({ id: cancelTarget, op: 'cancel' })
          }
          setCancelTarget(null)
        }}
        onCancel={() => setCancelTarget(null)}
      />
    </PageShell>
  )
}
