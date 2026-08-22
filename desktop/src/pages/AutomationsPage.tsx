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
  if (schedule.kind === 'once') {
    return schedule.run_at
      ? `Once · ${new Date(schedule.run_at).toLocaleString()}`
      : 'Once · time not set'
  }
  if (schedule.kind === 'interval') {
    const seconds = schedule.interval_seconds ?? 0
    if (seconds % 86_400 === 0) return `Every ${seconds / 86_400} day${seconds === 86_400 ? '' : 's'}`
    if (seconds % 3600 === 0) return `Every ${seconds / 3600} hour${seconds === 3600 ? '' : 's'}`
    if (seconds % 60 === 0) return `Every ${seconds / 60} minutes`
    return `Every ${seconds} seconds`
  }
  return `Scheduled · ${schedule.timezone}`
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
      subtitle="Scheduled work Vesta will run in the future."
      actions={
        <button
          className="btn btn-primary btn-sm"
          onClick={() => setShowForm((value) => !value)}
        >
          {showForm ? 'Close' : 'New automation'}
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
          title="No scheduled work"
          hint="Create an automation for work Vesta should repeat or run later."
          icon="automations"
        />
      ) : (
        <div className="automation-list">
          {automations.map((automation) => (
            <article key={automation.id} className={`automation-row automation-row--${automation.status}`}>
              <div className="automation-row__main">
                <div className="automation-row__heading">
                  <strong>{automation.title}</strong>
                  <span className={`automation-state automation-state--${automation.status}`}>{automation.status}</span>
                </div>
                <p>{automation.prompt}</p>
                <div className="automation-row__schedule">{scheduleText(automation.schedule)}</div>
              </div>
              <dl className="automation-row__timing">
                <div><dt>Next run</dt><dd>{formatTime(automation.next_run_at)}</dd></div>
                <div><dt>Last run</dt><dd>{formatTime(automation.last_run_at)}</dd></div>
              </dl>
              <details className="automation-row__details">
                <summary>Details</summary>
                <code>{automation.schedule.cron_expr ?? automation.schedule.kind} · {automation.schedule.timezone}</code>
              </details>
              <div className="automation-row__actions">
                    <button
                      className="btn btn-sm"
                      disabled={automation.status !== 'active'}
                      onClick={() => controlMutation.mutate({ id: automation.id, op: 'pause' })}
                    >
                      Pause
                    </button>
                    <button
                      className="btn btn-sm"
                      disabled={automation.status !== 'paused'}
                      onClick={() => controlMutation.mutate({ id: automation.id, op: 'resume' })}
                    >
                      Resume
                    </button>
                    <button
                      className="btn btn-sm btn-danger"
                      disabled={automation.status === 'cancelled' || automation.status === 'completed'}
                      onClick={() => setCancelTarget(automation.id)}
                    >
                      Cancel
                    </button>
              </div>
            </article>
          ))}
        </div>
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
