/** 当前 Run 的内联工作区：实时活动 + 模型文本增量。 */

import type { AgentEvent } from '../api/types'
import { Icon } from './Icon'
import { AssistantContent } from './MessageList'
import { ActivityItems } from './RunActivity'

export default function LiveAgentTurn({
  events,
  streamText,
}: {
  events: AgentEvent[]
  streamText: string
}): React.JSX.Element {
  return (
    <section className="live-turn" aria-live="polite" aria-label="oneAgent 实时执行过程">
      <div className="live-turn__header">
        <span className="message-assistant__avatar"><Icon name="agent" size={13} /></span>
        <strong>oneAgent is working</strong>
        <span className="live-turn__pulse" aria-hidden="true" />
      </div>
      <div className="live-turn__activity">
        {events.length > 0 ? (
          <ActivityItems events={events} />
        ) : (
          <div className="live-turn__starting">Starting the run…</div>
        )}
      </div>
      {streamText ? (
        <div className="live-turn__response">
          <AssistantContent content={streamText} />
          <span className="stream-cursor" aria-hidden="true" />
        </div>
      ) : null}
    </section>
  )
}
