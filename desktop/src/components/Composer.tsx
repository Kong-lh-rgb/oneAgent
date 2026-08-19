import { useState } from 'react'

export default function Composer({
  disabled,
  onSend,
}: {
  disabled: boolean
  onSend: (content: string) => Promise<void>
}): React.JSX.Element {
  const [value, setValue] = useState('')
  const [sending, setSending] = useState(false)

  const submit = async (): Promise<void> => {
    const content = value.trim()
    if (!content || sending || disabled) return
    setSending(true)
    try {
      await onSend(content)
      setValue('')
    } finally {
      setSending(false)
    }
  }

  return (
    <div style={{ borderTop: '1px solid var(--border)', padding: 10, display: 'flex', gap: 8 }}>
      <textarea
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault()
            void submit()
          }
        }}
        placeholder="输入消息，Enter 发送，Shift+Enter 换行"
        rows={2}
        style={{ flex: 1, resize: 'vertical' }}
        disabled={disabled || sending}
      />
      <button
        className="btn btn-primary"
        onClick={() => void submit()}
        disabled={disabled || sending || value.trim() === ''}
        style={{ alignSelf: 'flex-end' }}
      >
        {sending ? '发送中…' : '发送'}
      </button>
    </div>
  )
}
