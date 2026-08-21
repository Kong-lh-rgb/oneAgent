/** 独立浮动审批小窗（Electron 浮窗入口根组件）。

只处理 ``desktop`` 审批（ui_scope === 'desktop'）。审批一旦进入浮窗，
不会因主窗口焦点变化迁移到 Chat；批准后继续展示动作和 Run 状态。
可见性/尺寸通过 ``window.vesta`` 通知 Electron Main
（只有 Main 能 show/hide/resize BrowserWindow）。
*/

import { useEffect } from 'react'

import {
  floatingApprovalDismissDelay,
  floatingApprovalShouldBeVisible,
} from '../approval/floatingApprovalState'

import {
  ApprovalFloatingCard,
  useComputerApprovalQueue,
} from './ComputerApprovalPopup'

export default function ApprovalFloatingWindow(): React.JSX.Element | null {
  const { queue, active, busy, error, phase, resolve, dismiss } =
    useComputerApprovalQueue()
  const shouldShow = active !== null && floatingApprovalShouldBeVisible(phase)

  useEffect(() => {
    window.vesta?.setApprovalVisible?.(shouldShow)
  }, [shouldShow])

  // 成功/拒绝短暂停留，Run 失败多留一会；下一项审批由 dismiss 自动接棒。
  useEffect(() => {
    const delay = floatingApprovalDismissDelay(phase)
    if (delay === null || !active) return
    const timer = window.setTimeout(dismiss, delay)
    return () => window.clearTimeout(timer)
  }, [active, dismiss, phase])

  // 内容变化时把实际高度告诉 Main，让窗口贴合内容（避免黑色空白）。
  useEffect(() => {
    if (!shouldShow) return
    const element = document.body
    if (!element) return
    const sync = (): void => {
      const height = Math.ceil(element.scrollHeight)
      window.vesta?.setApprovalSize?.(height)
    }
    sync()
    const observer = new ResizeObserver(sync)
    observer.observe(element)
    return () => observer.disconnect()
  }, [shouldShow, active?.id, queue.length, busy, error, phase])

  if (!active || !shouldShow) return null

  return (
    <ApprovalFloatingCard
      approval={active}
      queuedCount={queue.length}
      busy={busy}
      error={error}
      phase={phase}
      onApprove={() => void resolve('approve')}
      onDeny={() => void resolve('deny')}
    />
  )
}
