import { useState } from 'react'

import type { CreateAutomationInput } from '../api/automations'
import type { AutomationKind } from '../api/types'

interface Props {
  onSubmit: (input: CreateAutomationInput) => Promise<void>
  onCancel: () => void
}

export default function AutomationForm({
  onSubmit,
  onCancel,
}: Props): React.JSX.Element {
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
      setError('请填写标题和执行指令。')
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
    <section className="automation-form">
      <header className="automation-form__header">
        <div>
          <span className="automation-form__eyebrow">新建自动化</span>
          <h2>让 Vesta 按时完成工作</h2>
          <p>设置执行内容和触发时间，保存后会由 Host 持续调度。</p>
        </div>
      </header>

      <div className="automation-form__section">
        <label className="automation-form__field">
          <span>名称</span>
          <input placeholder="例如：每日项目进展汇总" value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <label className="automation-form__field">
          <span>执行指令</span>
          <textarea
            placeholder="详细描述触发时希望 Vesta 完成的任务…"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            rows={4}
          />
        </label>
      </div>

      <div className="automation-form__section">
        <span className="automation-form__label">触发方式</span>
        <div className="automation-kind-picker">
          {([
            ['once', '单次执行', '在指定时间执行一次'],
            ['interval', '固定间隔', '按秒、分钟或小时循环'],
            ['cron', 'Cron 计划', '适合精确的日历调度'],
          ] as const).map(([value, label, hint]) => (
            <button
              key={value}
              type="button"
              className={kind === value ? 'active' : ''}
              onClick={() => setKind(value)}
            >
              <strong>{label}</strong><small>{hint}</small>
            </button>
          ))}
        </div>
      </div>

      <div className="automation-form__section automation-form__schedule">
      {kind === 'once' && (
        <label className="automation-form__field"><span>执行时间</span><input type="datetime-local" value={runAt} onChange={(e) => setRunAt(e.target.value)} /></label>
      )}
      {kind === 'interval' && (
        <label className="automation-form__field"><span>间隔秒数</span><input type="number" min="1" placeholder="3600" value={intervalSeconds} onChange={(e) => setIntervalSeconds(e.target.value)} /></label>
      )}
      {kind === 'cron' && (
        <label className="automation-form__field"><span>Cron 表达式</span><input placeholder="0 9 * * *" value={cronExpr} onChange={(e) => setCronExpr(e.target.value)} /></label>
      )}
        <label className="automation-form__field"><span>时区</span><input placeholder="Asia/Shanghai" value={timezone} onChange={(e) => setTimezone(e.target.value)} /></label>
      </div>
      {error && <div className="error-text automation-form__error">{error}</div>}
      <div className="automation-form__actions">
        <button className="btn btn-primary" onClick={() => void submit()} disabled={busy}>
          {busy ? '正在创建…' : '创建自动化'}
        </button>
        <button className="btn" onClick={onCancel}>取消</button>
      </div>
    </section>
  )
}


function toIsoWithOffset(localValue: string): string | null {
  if (!localValue) return null
  const date = new Date(localValue)
  if (Number.isNaN(date.getTime())) return null
  return date.toISOString()
}
