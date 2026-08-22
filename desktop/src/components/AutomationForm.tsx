import { useState } from 'react'

import type { CreateAutomationInput } from '../api/automations'
import type { AutomationKind } from '../api/types'

interface Props {
  onSubmit: (input: CreateAutomationInput) => Promise<void>
  onCancel: () => void
}

export default function AutomationForm({ onSubmit, onCancel }: Props): React.JSX.Element {
  const [title, setTitle] = useState('')
  const [prompt, setPrompt] = useState('')
  const [kind, setKind] = useState<AutomationKind>('once')
  const [runAt, setRunAt] = useState('')
  const [intervalSeconds, setIntervalSeconds] = useState('3600')
  const [cronExpr, setCronExpr] = useState('0 9 * * *')
  const [timezone, setTimezone] = useState('Asia/Shanghai')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (): Promise<void> => {
    setError(null)
    if (!title.trim() || !prompt.trim()) {
      setError('title 和 prompt 必填。')
      return
    }
    const input: CreateAutomationInput = {
      title: title.trim(),
      prompt: prompt.trim(),
      kind,
      timezone,
    }
    if (kind === 'once') {
      // 需要带时区偏移的 ISO8601；把 datetime-local 值转成 UTC ISO。
      const parsed = toIsoWithOffset(runAt)
      if (!parsed) {
        setError('请选择未来的一次性触发时间。')
        return
      }
      input.run_at = parsed
    } else if (kind === 'interval') {
      const seconds = Number(intervalSeconds)
      if (!Number.isFinite(seconds) || seconds <= 0) {
        setError('间隔秒数必须大于 0。')
        return
      }
      input.interval_seconds = seconds
    } else {
      if (!cronExpr.trim()) {
        setError('cron 表达式不能为空。')
        return
      }
      input.cron_expr = cronExpr.trim()
    }
    setBusy(true)
    try {
      await onSubmit(input)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="automation-form">
      <div className="automation-form__title">New scheduled work</div>
      <div className="automation-form__grid">
        <input placeholder="Title" value={title} onChange={(e) => setTitle(e.target.value)} />
        <select value={kind} onChange={(e) => setKind(e.target.value as AutomationKind)}>
          <option value="once">Once</option>
          <option value="interval">Recurring interval</option>
          <option value="cron">Schedule</option>
        </select>
      </div>
      <textarea
        placeholder="What should Vesta do when this runs?"
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        rows={2}
        className="automation-form__prompt"
      />
      {kind === 'once' && (
        <div className="automation-form__grid automation-form__conditional">
          <input
            type="datetime-local"
            value={runAt}
            onChange={(e) => setRunAt(e.target.value)}
          />
          <input placeholder="Timezone (Asia/Shanghai)" value={timezone} onChange={(e) => setTimezone(e.target.value)} />
        </div>
      )}
      {kind === 'interval' && (
        <div className="automation-form__grid automation-form__conditional">
          <input
            type="number"
            min="1"
            placeholder="Interval in seconds"
            value={intervalSeconds}
            onChange={(e) => setIntervalSeconds(e.target.value)}
          />
          <input placeholder="Timezone" value={timezone} onChange={(e) => setTimezone(e.target.value)} />
        </div>
      )}
      {kind === 'cron' && (
        <div className="automation-form__grid automation-form__conditional">
          <input placeholder='Schedule expression, e.g. "0 9 * * *"' value={cronExpr} onChange={(e) => setCronExpr(e.target.value)} />
          <input placeholder="Timezone" value={timezone} onChange={(e) => setTimezone(e.target.value)} />
        </div>
      )}
      {error && <div className="error-text automation-form__error">{error}</div>}
      <div className="automation-form__actions">
        <button className="btn btn-primary" onClick={() => void submit()} disabled={busy}>
          {busy ? 'Creating…' : 'Create'}
        </button>
        <button className="btn" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  )
}

function toIsoWithOffset(localValue: string): string | null {
  if (!localValue) return null
  const date = new Date(localValue)
  if (Number.isNaN(date.getTime())) return null
  return date.toISOString()
}
