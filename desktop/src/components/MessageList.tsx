import type { Message } from '../api/types'

/** 极简 Markdown 渲染：只处理粗体 / 行内代码 / 代码块 / 换行。 */
function renderInline(text: string): React.ReactNode[] {
  const parts: React.ReactNode[] = []
  const regex = /(\*\*[^*]+\*\*|`[^`]+`)/g
  let lastIndex = 0
  let match: RegExpExecArray | null
  let key = 0
  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index))
    }
    const token = match[0]
    if (token.startsWith('**')) {
      parts.push(
        <strong key={key++}>{token.slice(2, -2)}</strong>,
      )
    } else {
      parts.push(<code key={key++}>{token.slice(1, -1)}</code>)
    }
    lastIndex = match.index + token.length
  }
  if (lastIndex < text.length) parts.push(text.slice(lastIndex))
  return parts
}

function renderBlocks(text: string): React.ReactNode[] {
  const lines = text.split('\n')
  const blocks: React.ReactNode[] = []
  let codeBuffer: string[] = []
  let inCode = false
  let key = 0

  const flushCode = (): void => {
    if (codeBuffer.length > 0) {
      blocks.push(
        <pre key={key++} style={{ background: 'var(--bg-panel)', padding: 10, borderRadius: 6, overflowX: 'auto' }}>
          <code>{codeBuffer.join('\n')}</code>
        </pre>,
      )
      codeBuffer = []
    }
  }

  for (const line of lines) {
    if (line.startsWith('```')) {
      if (inCode) {
        flushCode()
        inCode = false
      } else {
        flushCode()
        inCode = true
      }
      continue
    }
    if (inCode) {
      codeBuffer.push(line)
      continue
    }
    if (line.trim() === '') {
      blocks.push(<div key={key++} style={{ height: 8 }} />)
      continue
    }
    blocks.push(<p key={key++} style={{ margin: 0 }}>{renderInline(line)}</p>)
  }
  if (inCode) flushCode()
  return blocks
}

export default function MessageList({ messages }: { messages: Message[] }): React.JSX.Element {
  if (messages.length === 0) {
    return <div className="empty">还没有消息。发送一句话开始。</div>
  }
  return (
    <div>
      {messages
        .filter((message) => message.role !== 'system')
        .map((message, index) => {
          if (message.role === 'user') {
            return (
              <div key={index} style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 12 }}>
                <div
                  style={{
                    maxWidth: '78%',
                    background: 'var(--accent-soft)',
                    border: '1px solid var(--accent)',
                    borderRadius: 12,
                    padding: '8px 12px',
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-word',
                  }}
                >
                  {message.content}
                </div>
              </div>
            )
          }
          if (message.role === 'assistant') {
            return (
              <div key={index} style={{ display: 'flex', justifyContent: 'flex-start', marginBottom: 12 }}>
                <div
                  style={{
                    maxWidth: '85%',
                    background: 'var(--bg-panel)',
                    border: '1px solid var(--border)',
                    borderRadius: 12,
                    padding: '8px 12px',
                    wordBreak: 'break-word',
                  }}
                >
                  {renderBlocks(message.content ?? '')}
                </div>
              </div>
            )
          }
          return null
        })}
    </div>
  )
}
