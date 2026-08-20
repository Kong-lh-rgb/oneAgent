import type { Message } from '../api/types'
import { Icon } from './Icon'
import { EmptyState } from './ui'
import { AssistantContent } from './AssistantContent'
import AssistantReasoning from './AssistantReasoning'

export { AssistantContent }

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
                <AssistantReasoning text={message.reasoning ?? ''} />
                <AssistantContent content={message.content ?? ''} />
              </div>
            )
          }
          return null
        })}
    </div>
  )
}
