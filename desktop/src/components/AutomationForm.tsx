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
    <div style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: 8, padding: 14, marginBottom: 12 }}>
      <div style={{ fontWeight: 600, marginBottom: 10 }}>新建 Automation</div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
        <input placeholder="标题" value={title} onChange={(e) => setTitle(e.target.value)} />
        <select value={kind} onChange={(e) => setKind(e.target.value as AutomationKind)}>
          <option value="once">once（一次性）</option>
          <option value="interval">interval（固定间隔）</option>
          <option value="cron">cron（计划表达式）</option>
        </select>
      </div>
      <textarea
        placeholder="触发时真正要执行的指令（不要包含调度条件）"
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        rows={2}
        style={{ width: '100%', marginTop: 10 }}
      />
      {kind === 'once' && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginTop: 10 }}>
          <input
            type="datetime-local"
            value={runAt}
            onChange={(e) => setRunAt(e.target.value)}
          />
          <input placeholder="时区（默认 Asia/Shanghai）" value={timezone} onChange={(e) => setTimezone(e.target.value)} />
        </div>
      )}
      {kind === 'interval' && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginTop: 10 }}>
          <input
            type="number"
            min="1"
            placeholder="间隔秒数"
            value={intervalSeconds}
            onChange={(e) => setIntervalSeconds(e.target.value)}
          />
          <input placeholder="时区" value={timezone} onChange={(e) => setTimezone(e.target.value)} />
        </div>
      )}
      {kind === 'cron' && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginTop: 10 }}>
          <input placeholder='cron 表达式，如 "0 9 * * *"' value={cronExpr} onChange={(e) => setCronExpr(e.target.value)} />
          <input placeholder="时区" value={timezone} onChange={(e) => setTimezone(e.target.value)} />
        </div>
      )}
      {error && <div className="error-text" style={{ marginTop: 8 }}>{error}</div>}
      <div style={{ marginTop: 10, display: 'flex', gap: 8 }}>
        <button className="btn btn-primary" onClick={() => void submit()} disabled={busy}>
          {busy ? '创建中…' : '创建'}
        </button>
        <button className="btn" onClick={onCancel}>取消</button>
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
