import { memo } from 'react'
import type { Message } from '../api/types'
import { Icon } from './Icon'
import { EmptyState } from './ui'
import { AssistantContent } from './AssistantContent'
import AssistantReasoning from './AssistantReasoning'

export { AssistantContent }

/** 渲染单元：一次“角色块”。同一轮回复的多条连续 assistant 消息合并成一组，
   只显示一次作者行（头像），避免每个 step 落库消息都带一个头像。 */
interface RenderedTurn {
  key: number
  role: 'user' | 'assistant'
  /** 是否显示作者行（头像）。组内第一条为 true，后续连续 assistant 为 false。 */
  author: boolean
  content: string
  reasoning?: string
}

function buildThread(messages: Message[]): RenderedTurn[] {
  const out: RenderedTurn[] = []
  let assistantOpen = false

  messages.forEach((message, index) => {
    if (message.role === 'system') return
    if (message.role === 'user') {
      assistantOpen = false
      out.push({
        key: index,
        role: 'user',
        author: true,
        content: message.content ?? '',
      })
      return
    }
    // assistant
    // 1) 带工具调用的中间消息（tool_calls）：协议噪音，无论正文是否为空一律
    //    不渲染 —— 模型一轮回复里可能有很多步，避免回复区出现长串工具调用。
    if (message.tool_calls && message.tool_calls.length > 0) {
      return
    }
    // 2) 无正文且无思考的空消息：同样跳过，让一次回复到最后只有一个头像。
    const content = message.content ?? ''
    const reasoning = message.reasoning ?? ''
    if (!content && !reasoning) return

    out.push({
      key: index,
      role: 'assistant',
      author: !assistantOpen,
      content,
      reasoning,
    })
    assistantOpen = true
  })

  return out
}

/** memo：messages 引用未变时跳过整棵树渲染，避免流式事件导致历史消息反复 markdown 解析。 */
export default memo(function MessageList({
  messages,
}: {
  messages: Message[]
}): React.JSX.Element {
  if (messages.length === 0) {
    return (
      <EmptyState
        title="开始对话"
        hint="向 Vesta 描述你想做的事，Enter 发送。"
      />
    )
  }
  const thread = buildThread(messages)
  return (
    <div>
      {thread.map((turn) => {
        if (turn.role === 'user') {
          return (
            <div key={turn.key} className="message-user">
              <div className="message-user__body">{turn.content}</div>
            </div>
          )
        }
        if (!turn.author) {
          // 同一条回复的延续：无头像，仅正文（保持与首块一致的底部间距）。
          return (
            <div
              key={turn.key}
              className="message-assistant message-assistant--continuation"
            >
              <AssistantReasoning text={turn.reasoning ?? ''} />
              <AssistantContent content={turn.content} />
            </div>
          )
        }
        return (
          <div key={turn.key} className="message-assistant">
            <div className="message-assistant__author">
              <span className="message-assistant__avatar"><Icon name="agent" size={13} /></span>
              Vesta
            </div>
            <AssistantReasoning text={turn.reasoning ?? ''} />
            <AssistantContent content={turn.content} />
          </div>
        )
      })}
    </div>
  )
})
