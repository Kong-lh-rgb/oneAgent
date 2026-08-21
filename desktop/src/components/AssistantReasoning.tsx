/** 模型思考/推理过程（reasoning）展示：可折叠下拉栏，与最终答案彻底分开。

- 下拉栏：默认收起，点击展开（grid 0fr→1fr 高度过渡，丝滑）。
- autoExpand：思考进行中（尚无正文）时自动展开展示；正文开始流出时自动
  平滑收起，完成“思考 → 答案”的无缝过渡。
- 用户手动点过之后尊重用户选择（直到下一轮思考重新接管）。
- label：思考中 “Thinking”，完成后折叠 “Thought for 1.8s”（传入 durationMs）。
*/

import { useEffect, useRef, useState } from 'react'
import type { ReactElement } from 'react'

import { formatDuration } from '../agent/turnPresentation'

export default function AssistantReasoning({
  text,
  autoExpand = false,
  busy = false,
  durationMs = null,
}: {
  text: string
  /** 思考仍在进行（正文尚未出现）时置 true：自动展开并跟随。 */
  autoExpand?: boolean
  /** 思考中（显示 spinner 与 “Thinking”）。 */
  busy?: boolean
  /** 思考耗时（思考完成、折叠时显示 “Thought for 1.8s”）。 */
  durationMs?: number | null
}): ReactElement | null {
  const [open, setOpen] = useState(autoExpand)
  const [userPinned, setUserPinned] = useState(false)
  const prevAutoExpand = useRef(autoExpand)

  useEffect(() => {
    const wasAuto = prevAutoExpand.current
    prevAutoExpand.current = autoExpand
    if (autoExpand) {
      // 新一轮思考开始 → 接管并展开。
      setUserPinned(false)
      setOpen(true)
    } else if (wasAuto && !autoExpand && !userPinned) {
      // 思考结束、正文出现 → 平滑收起（除非用户手动展开过）。
      setOpen(false)
    }
  }, [autoExpand, userPinned])

  if (!text) return null

  const label = busy
    ? 'Thinking'
    : durationMs !== null
      ? `Thought for ${formatDuration(durationMs)}`
      : 'Thinking'

  return (
    <div
      className={`assistant-reasoning${open ? ' assistant-reasoning--open' : ''}`}
      data-testid="assistant-reasoning"
    >
      <button
        type="button"
        className="assistant-reasoning__toggle"
        aria-expanded={open}
        onClick={() => {
          setUserPinned(true)
          setOpen((value) => !value)
        }}
      >
        <span className="assistant-reasoning__chevron" aria-hidden="true" />
        {busy ? (
          <span className="assistant-reasoning__spinner" aria-hidden="true" />
        ) : null}
        <span className="assistant-reasoning__label">{label}</span>
      </button>
      <div className="assistant-reasoning__wrap">
        <div className="assistant-reasoning__body">{text}</div>
      </div>
    </div>
  )
}
