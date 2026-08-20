/** 新会话的工作入口：轻量欢迎语与可填入 Composer 的示例任务。 */

import type { ReactElement } from 'react'

export const EXAMPLE_PROMPTS = [
  'Organize a set of files',
  'Research and write a report',
  'Plan a multi-step task',
  'Work with an app on this Mac',
] as const

export default function ChatEmptyState({
  onSelectPrompt,
}: {
  onSelectPrompt: (prompt: string) => void
}): ReactElement {
  return (
    <section className="chat-empty" aria-label="开始新会话">
      <div className="chat-empty__mark">oa</div>
      <h1>What can I work on for you?</h1>
      <p>Give oneAgent a goal. It can plan, use tools, and keep working across runs.</p>
      <div className="chat-empty__prompts">
        {EXAMPLE_PROMPTS.map((prompt) => (
          <button key={prompt} type="button" onClick={() => onSelectPrompt(prompt)}>
            {prompt}
          </button>
        ))}
      </div>
    </section>
  )
}
