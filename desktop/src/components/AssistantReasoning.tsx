/** 模型思考/推理过程（reasoning）展示：可折叠、低调、不打断正文。 */

import type { ReactElement } from 'react'

export default function AssistantReasoning({
  text,
}: {
  text: string
}): ReactElement | null {
  if (!text) return null
  return (
    <details className="assistant-reasoning" data-testid="assistant-reasoning">
      <summary>
        <span className="assistant-reasoning__label">思考过程</span>
      </summary>
      <div className="assistant-reasoning__body">{text}</div>
    </details>
  )
}
