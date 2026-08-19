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
  const [notice, setNotice] = useState<string | null>(null)

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
      setNotice('已创建。')
      invalidate()
    },
    onError: (err: unknown) => {
      setNotice(err instanceof Error ? err.message : String(err))
    },
  })

  const controlMutation = useMutation({
    mutationFn: (action: { id: string; op: 'pause' | 'resume' | 'cancel' }) => {
      if (action.op === 'pause') return pauseAutomation(action.id)
      if (action.op === 'resume') return resumeAutomation(action.id)
      return cancelAutomation(action.id)
    },
    onSuccess: () => {
      invalidate()
    },
  })

  return (
    <div style={{ padding: 16, overflowY: 'auto', flex: 1 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
        <h2 style={{ margin: 0, fontSize: 16 }}>Automations</h2>
        <button className="btn btn-primary btn-sm" onClick={() => setShowForm((value) => !value)}>
          {showForm ? '收起' : '＋ 新建'}
        </button>
      </div>

      {notice && <div className="text-dim" style={{ marginBottom: 10 }}>{notice}</div>}

      {showForm && (
        <AutomationForm
          onSubmit={async (input) => {
            await createMutation.mutateAsync(input)
          }}
          onCancel={() => setShowForm(false)}
        />
      )}

      {automations.length === 0 ? (
        <div className="empty">暂无 Automation。</div>
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
                      onClick={() => controlMutation.mutate({ id: automation.id, op: 'cancel' })}
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
    </div>
  )
}
