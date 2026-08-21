/** 独立浮动审批小窗（Electron 浮窗入口根组件）。

只处理 ``computer_*`` 审批：有审批时浮窗可见，队列清空后自动隐藏。
可见性通过 ``window.vesta.setApprovalVisible`` 通知 Electron Main
（只有 Main 能 show/hide BrowserWindow）。
*/

import { useEffect } from 'react'

import {
  ApprovalFloatingCard,
  useComputerApprovalQueue,
} from './ComputerApprovalPopup'

export default function ApprovalFloatingWindow(): React.JSX.Element | null {
  const { queue, active, busy, error, resolve } = useComputerApprovalQueue()
  const hasApproval = queue.length > 0

  // 队列非空 → 让主进程显示浮窗；清空 → 隐藏。
  useEffect(() => {
    window.vesta?.setApprovalVisible?.(hasApproval)
  }, [hasApproval])

  if (!active) return null

  return (
    <ApprovalFloatingCard
      approval={active}
      queuedCount={queue.length}
      busy={busy}
      error={error}
      onApprove={() => void resolve('approve')}
      onDeny={() => void resolve('deny')}
    />
  )
}
