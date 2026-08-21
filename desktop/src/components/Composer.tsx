import { useEffect, useRef, useState } from 'react'

import type { AgentMode } from '../api/types'
import { Icon } from './Icon'
import type { IconName } from './Icon'

export interface ComposerCommand {
  id: string
  label: string
  icon?: IconName
  onSelect: () => void
}

export interface ComposerProps {
  disabled: boolean
  sending?: boolean
  mode?: AgentMode
  onModeChange?: (mode: AgentMode) => void
  onSend: (content: string) => Promise<void>
  value?: string
  onValueChange?: (value: string) => void
  /** 轻量 Command palette 项（⌘K）。 */
  commands?: ComposerCommand[]
}

export default function Composer({
  disabled,
  sending = false,
  mode = 'normal',
  onModeChange,
  onSend,
  value,
  onValueChange,
  commands,
}: ComposerProps): React.JSX.Element {
  const [internalValue, setInternalValue] = useState('')
  const [commandOpen, setCommandOpen] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const draft = value ?? internalValue
  const busy = disabled || sending
  const canSend = draft.trim() !== '' && !busy

  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        setCommandOpen((open) => !open)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

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
          placeholder="Ask Vesta to do something…"
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
          {commands && commands.length > 0 ? (
            <button
              type="button"
              className={`composer__cmd ${commandOpen ? 'active' : ''}`}
              onClick={() => setCommandOpen((open) => !open)}
              aria-expanded={commandOpen}
              title="Commands (⌘K)"
              aria-label="Commands"
            >
              ⌘K
            </button>
          ) : null}
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
      {commands && commandOpen ? (
        <div className="composer-commands" role="menu" aria-label="Commands">
          {commands.map((command) => (
            <button
              key={command.id}
              type="button"
              role="menuitem"
              className="composer-commands__item"
              onClick={() => {
                setCommandOpen(false)
                command.onSelect()
              }}
            >
              {command.icon ? <Icon name={command.icon} size={14} /> : null}
              {command.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  )
}
