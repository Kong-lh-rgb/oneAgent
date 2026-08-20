import type { Message } from '../api/types'
import { Icon } from './Icon'
import { EmptyState } from './ui'

/** 轻量 Markdown 渲染：覆盖 Agent 回复里最常用的文档层级，不引入 HTML 注入。 */
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
        <pre key={key++}>
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
      blocks.push(<div key={key++} className="message-assistant__spacer" />)
      continue
    }
    const heading = /^(#{1,3})\s+(.+)$/.exec(line)
    if (heading) {
      const content = renderInline(heading[2])
      const level = heading[1].length
      if (level === 1) blocks.push(<h2 key={key++}>{content}</h2>)
      else if (level === 2) blocks.push(<h3 key={key++}>{content}</h3>)
      else blocks.push(<h4 key={key++}>{content}</h4>)
      continue
    }
    const unordered = /^[-*]\s+(.+)$/.exec(line)
    if (unordered) {
      blocks.push(
        <div key={key++} className="message-assistant__list-item">
          <span aria-hidden="true">•</span>
          <p>{renderInline(unordered[1])}</p>
        </div>,
      )
      continue
    }
    const ordered = /^(\d+)\.\s+(.+)$/.exec(line)
    if (ordered) {
      blocks.push(
        <div key={key++} className="message-assistant__list-item">
          <span>{ordered[1]}.</span>
          <p>{renderInline(ordered[2])}</p>
        </div>,
      )
      continue
    }
    blocks.push(<p key={key++}>{renderInline(line)}</p>)
  }
  if (inCode) flushCode()
  return blocks
}

export function AssistantContent({ content }: { content: string }): React.JSX.Element {
  return <div className="message-assistant__body">{renderBlocks(content)}</div>
}

export default function MessageList({ messages }: { messages: Message[] }): React.JSX.Element {
  if (messages.length === 0) {
    return (
      <EmptyState
        title="开始对话"
        hint="向 oneAgent 描述你想做的事，Enter 发送。"
      />
    )
  }
  return (
    <div>
      {messages
        .filter((message) => message.role !== 'system')
        .map((message, index) => {
          if (message.role === 'user') {
            return (
              <div key={index} className="message-user">
                <div className="message-user__body">{message.content}</div>
              </div>
            )
          }
          if (message.role === 'assistant') {
            return (
              <div key={index} className="message-assistant">
                <div className="message-assistant__author">
                  <span className="message-assistant__avatar"><Icon name="agent" size={13} /></span>
                  oneAgent
                </div>
                <AssistantContent content={message.content ?? ''} />
              </div>
            )
          }
          return null
        })}
    </div>
  )
}
