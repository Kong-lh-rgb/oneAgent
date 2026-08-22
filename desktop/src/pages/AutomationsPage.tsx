import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import {
  cancelAutomation,
  listAutomations,
  pauseAutomation,
  resumeAutomation,
} from '../api/automations'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { EmptyState, ErrorState, LoadingState } from '../components/PageStates'
import { Icon } from '../components/Icon'
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
  return `${schedule.cron_expr ?? 'Cron'} · ${schedule.timezone}`
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
      subtitle="查看并管理 Vesta 根据你的要求创建的定时工作。"
    >
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
          hint="在对话中告诉 Vesta 需要何时执行什么工作，创建后会显示在这里。"
          icon="automations"
        />
      ) : (
        <div className="automation-list">
          {automations.map((automation) => (
            <article key={automation.id} className={`automation-card automation-card--${automation.status}`}>
              <header className="automation-card__header">
                <div className="automation-card__identity">
                  <span className="automation-card__icon"><Icon name="automations" size={16} /></span>
                  <div>
                    <strong>{automation.title}</strong>
                    <span className="mono">{automation.id.slice(0, 8)}</span>
                  </div>
                </div>
                <span className={`automation-state automation-state--${automation.status}`}>{automationStatusLabel(automation.status)}</span>
              </header>

              <details className="automation-card__description">
                <summary><span>查看任务描述</span><Icon name="chevronDown" size={14} /></summary>
                <p>{automation.prompt}</p>
              </details>

              <div className="automation-card__schedule">
                <span>调度计划</span>
                <strong>{scheduleText(automation.schedule)}</strong>
              </div>

              <dl className="automation-card__timing">
                <div><dt>下次执行</dt><dd>{formatTime(automation.next_run_at)}</dd></div>
                <div><dt>上次执行</dt><dd>{formatTime(automation.last_run_at)}</dd></div>
              </dl>

              <details className="automation-card__details">
                <summary><span>查看计划详情</span><Icon name="chevronDown" size={14} /></summary>
                <dl>
                  <div><dt>类型</dt><dd>{automation.schedule.kind === 'once' ? '单次' : automation.schedule.kind === 'interval' ? '固定间隔' : 'Cron'}</dd></div>
                  <div><dt>时区</dt><dd>{automation.schedule.timezone}</dd></div>
                  <div><dt>调度值</dt><dd className="mono">{automation.schedule.cron_expr ?? automation.schedule.interval_seconds ?? automation.schedule.run_at ?? '—'}</dd></div>
                  <div><dt>最近 Run</dt><dd className="mono">{automation.last_run_id?.slice(0, 8) ?? '—'}</dd></div>
                </dl>
              </details>

              <footer className="automation-card__footer">
                <span className="automation-card__updated">更新于 {formatTime(automation.updated_at)}</span>
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
              </footer>
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
