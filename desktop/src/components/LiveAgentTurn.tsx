/** 当前 Run 的内联工作区：实时活动 + 模型文本增量。 */

import type { AgentEvent } from '../api/types'
import { Icon } from './Icon'
import { AssistantContent } from './AssistantContent'
import AssistantReasoning from './AssistantReasoning'

export default function LiveAgentTurn({
  events,
  streamText,
  reasoning,
  settling = false,
}: {
  events: AgentEvent[]
  streamText: string
  reasoning?: string
  settling?: boolean
}): React.JSX.Element {
  // 工具调用等英文活动列表不再内联展示；用户想看细节走右侧 Activity 抽屉。
  void events

  return (
    <section
      className={`live-turn${settling ? ' live-turn--settling' : ''}`}
      aria-live="polite"
      aria-label="Vesta 实时执行过程"
    >
      {/* 与最终 assistant 消息共用的作者行，仅多一个 live 指示点：
          回复完成、容器被正式消息替换时，视觉不会跳变。 */}
      <div className="message-assistant__author">
        <span className="message-assistant__avatar"><Icon name="agent" size={13} /></span>
        Vesta
        <span className="live-turn__pulse" aria-hidden="true" />
      </div>
      {/* 思考仍在进行（正文还没开始）时自动展开；正文流出后自动平滑收起，
          思考与最终答案用独立容器彻底分开。 */}
      <AssistantReasoning
        text={reasoning ?? ''}
        autoExpand={!streamText}
        busy={!streamText}
      />
      {streamText ? (
        <div className="live-turn__response">
          <AssistantContent content={streamText} />
          <span className="stream-cursor" aria-hidden="true" />
        </div>
      ) : !reasoning ? (
        <div className="live-turn__waiting">
          <span className="live-turn__waiting-spinner" aria-hidden="true" />
          正在执行…
        </div>
      ) : null}
    </section>
  )
}
