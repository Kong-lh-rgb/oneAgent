/** 新会话的工作入口：轻量欢迎语与可填入 Composer 的示例任务。 */

import type { ReactElement } from 'react'

export const EXAMPLE_PROMPTS = [
  'Open Notes and add today’s ideas',
  'Organize files in a folder',
  'Create a recurring morning brief',
  'Investigate something and make a plan',
] as const

export default function ChatEmptyState({
  onSelectPrompt,
}: {
  onSelectPrompt: (prompt: string) => void
}): ReactElement {
  return (
    <section className="chat-empty" aria-label="开始新会话">
      <div className="chat-empty__mark">V</div>
      <h1>What should Vesta work on?</h1>
      <p>Give Vesta an outcome. You can follow the work, approve actions, and inspect results.</p>
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
