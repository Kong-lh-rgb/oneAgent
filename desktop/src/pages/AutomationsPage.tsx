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
      ? `单次 · ${new Date(schedule.run_at).toLocaleString('zh-CN')}`
      : '单次 · 未设置时间'
  }
  if (schedule.kind === 'interval') {
    const seconds = schedule.interval_seconds ?? 0
    if (seconds % 86_400 === 0) return `每 ${seconds / 86_400} 天`
    if (seconds % 3600 === 0) return `每 ${seconds / 3600} 小时`
    if (seconds % 60 === 0) return `每 ${seconds / 60} 分钟`
    return `每 ${seconds} 秒`
  }
  return `定时计划 · ${schedule.timezone}`
}

function formatTime(iso: string | null): string {
  if (!iso) return '-'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleString('zh-CN')
}

function automationStatusLabel(status: string): string {
  if (status === 'active') return '运行中'
  if (status === 'paused') return '已暂停'
  if (status === 'cancelled') return '已取消'
  if (status === 'completed') return '已完成'
  return status
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
      toast.success('自动化已创建')
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
      toast.info(`自动化${verb}`)
      invalidate()
    },
    onError: (err: unknown) => {
      toast.error(err instanceof Error ? err.message : String(err))
    },
  })

  return (
    <PageShell
      title="自动化"
      subtitle="让 Vesta 在指定时间或周期内自动执行工作。"
      actions={
        <button
          className="btn btn-primary btn-sm"
          onClick={() => setShowForm((value) => !value)}
        >
          {showForm ? '收起' : '新建自动化'}
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
        <LoadingState label="正在加载自动化…" />
      ) : automationsQuery.isError ? (
        <ErrorState
          message={String(automationsQuery.error)}
          onRetry={() => void automationsQuery.refetch()}
        />
      ) : automations.length === 0 ? (
        <EmptyState
          title="暂无自动化"
          hint="可以创建稍后执行或周期重复的工作。"
          icon="automations"
        />
      ) : (
        <div className="automation-list">
          {automations.map((automation) => (
            <article key={automation.id} className={`automation-row automation-row--${automation.status}`}>
              <div className="automation-row__main">
                <div className="automation-row__heading">
                  <strong>{automation.title}</strong>
                  <span className={`automation-state automation-state--${automation.status}`}>{automationStatusLabel(automation.status)}</span>
                </div>
                <p>{automation.prompt}</p>
                <div className="automation-row__schedule">{scheduleText(automation.schedule)}</div>
              </div>
              <dl className="automation-row__timing">
                <div><dt>下次执行</dt><dd>{formatTime(automation.next_run_at)}</dd></div>
                <div><dt>上次执行</dt><dd>{formatTime(automation.last_run_at)}</dd></div>
              </dl>
              <details className="automation-row__details">
                <summary>计划详情</summary>
                <code>{automation.schedule.cron_expr ?? automation.schedule.kind} · {automation.schedule.timezone}</code>
              </details>
              <div className="automation-row__actions">
                    <button
                      className="btn btn-sm"
                      disabled={automation.status !== 'active'}
                      onClick={() => controlMutation.mutate({ id: automation.id, op: 'pause' })}
                    >
                      暂停
                    </button>
                    <button
                      className="btn btn-sm"
                      disabled={automation.status !== 'paused'}
                      onClick={() => controlMutation.mutate({ id: automation.id, op: 'resume' })}
                    >
                      恢复
                    </button>
                    <button
                      className="btn btn-sm btn-danger"
                      disabled={automation.status === 'cancelled' || automation.status === 'completed'}
                      onClick={() => setCancelTarget(automation.id)}
                    >
                      取消
                    </button>
              </div>
            </article>
          ))}
        </div>
      )}
      <ConfirmDialog
        open={cancelTarget !== null}
        title="取消这个自动化？"
        message="取消后该定时任务将不再触发，且无法恢复。"
        confirmLabel="取消自动化"
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
