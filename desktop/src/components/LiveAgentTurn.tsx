/** 当前 Run 的内联工作区：实时活动 + 模型文本增量。 */

import type { AgentEvent } from '../api/types'
import { Icon } from './Icon'
import { AssistantContent } from './AssistantContent'
import { ActivityItems } from './RunActivity'

export default function LiveAgentTurn({
  events,
  streamText,
  settling = false,
}: {
  events: AgentEvent[]
  streamText: string
  settling?: boolean
}): React.JSX.Element {
  return (
    <section
      className={`live-turn${settling ? ' live-turn--settling' : ''}`}
      aria-live="polite"
      aria-label="oneAgent 实时执行过程"
    >
      {/* 与最终 assistant 消息共用的作者行，仅多一个 live 指示点：
          回复完成、容器被正式消息替换时，视觉不会跳变。 */}
      <div className="message-assistant__author">
        <span className="message-assistant__avatar"><Icon name="agent" size={13} /></span>
        oneAgent
        <span className="live-turn__pulse" aria-hidden="true" />
      </div>
      {events.length > 0 ? (
        <div className="live-turn__activity">
          <ActivityItems events={events} />
        </div>
      ) : (
        <div className="live-turn__starting">Starting the run…</div>
      )}
      {streamText ? (
        <div className="live-turn__response">
          <AssistantContent content={streamText} />
          <span className="stream-cursor" aria-hidden="true" />
        </div>
      ) : null}
    </section>
  )
}
