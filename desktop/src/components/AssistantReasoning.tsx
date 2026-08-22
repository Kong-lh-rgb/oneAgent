/** 模型思考/推理过程（reasoning）展示：与最终答案彻底分开。

- 只渲染一个「下拉尖」toggle，内联在 Vesta 头像行（与头像平行）。
- 点击展开/收起思考内容（grid 0fr→1fr 高度过渡，丝滑）。
- 思考中不自动展开、不占正文位置：正文区只放最终回复，思考内容仅在被
  点击展开时显示。
- label（Thinking / Thought for 1.8s）作为 title/aria-label，不占可见文本。
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
  /** 思考中（显示 spinner）。 */
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
        aria-label={open ? '收起思考过程' : '展示思考过程'}
        title={label}
        onClick={() => {
          setUserPinned(true)
          setOpen((value) => !value)
        }}
      >
        {busy ? (
          <span className="assistant-reasoning__spinner" aria-hidden="true" />
        ) : null}
        <span className="assistant-reasoning__chevron" aria-hidden="true" />
      </button>
      <div className="assistant-reasoning__wrap">
        <div className="assistant-reasoning__body">{text}</div>
      </div>
    </div>
  )
}
