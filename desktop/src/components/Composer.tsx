import { useEffect, useRef, useState } from 'react'

import type { AgentMode } from '../api/types'
import { Icon } from './Icon'

export interface ComposerProps {
  disabled: boolean
  sending?: boolean
  mode?: AgentMode
  onModeChange?: (mode: AgentMode) => void
  onSend: (content: string) => Promise<void>
  value?: string
  onValueChange?: (value: string) => void
}

export default function Composer({
  disabled,
  sending = false,
  mode = 'normal',
  onModeChange,
  onSend,
  value,
  onValueChange,
}: ComposerProps): React.JSX.Element {
  const [internalValue, setInternalValue] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const draft = value ?? internalValue
  const busy = disabled || sending
  const canSend = draft.trim() !== '' && !busy

  const setDraft = (next: string): void => {
    if (value === undefined) setInternalValue(next)
    onValueChange?.(next)
  }

  useEffect(() => {
    const textarea = textareaRef.current
    if (!textarea) return
    textarea.style.height = '0px'
    textarea.style.height = `${Math.min(textarea.scrollHeight, 160)}px`
  }, [draft])

  const submit = async (): Promise<void> => {
    const content = draft.trim()
    if (!content || busy) return
    setDraft('')
    try {
      await onSend(content)
    } catch {
      // 发送失败时恢复草稿，避免用户输入丢失。
      setDraft(content)
    }
  }

  return (
    <div className="composer-dock">
      <div className={`composer ${busy ? 'composer--busy' : ''}`}>
        <textarea
          ref={textareaRef}
          className="composer__input"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              void submit()
            }
          }}
          placeholder="Ask oneAgent to do something…"
          rows={1}
          disabled={busy}
          aria-label="消息输入框"
        />
        <div className="composer__footer">
          <div className="mode-switch" aria-label="Agent mode">
            {(['normal', 'plan'] as const).map((item) => (
              <button
                key={item}
                type="button"
                className={`mode-switch__item ${mode === item ? 'active' : ''}`}
                onClick={() => onModeChange?.(item)}
                aria-pressed={mode === item}
                title={
                  item === 'plan'
                    ? 'Plan Mode 会先调查并生成计划，等待你确认后再执行。'
                    : 'Normal Mode 直接执行你的请求。'
                }
                disabled={busy || !onModeChange}
              >
                {item === 'normal' ? 'Normal' : 'Plan'}
              </button>
            ))}
          </div>
          <span className="composer__hint">Enter 发送 · Shift+Enter 换行</span>
          <button
            type="button"
            className="composer__send"
            onClick={() => void submit()}
            disabled={!canSend}
            aria-label={sending ? '正在发送' : '发送'}
            title="发送"
          >
            {sending ? <span className="spinner spinner--light" /> : <Icon name="send" size={16} />}
          </button>
        </div>
      </div>
    </div>
  )
}
