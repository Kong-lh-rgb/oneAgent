import type { RunStatus } from '../api/types'

const ORDER: RunStatus[] = [
  'pending',
  'running',
  'completed',
  'failed',
  'cancelled',
  'interrupted',
]

export default function RunBadge({ status }: { status: RunStatus }): React.JSX.Element {
  const labels: Record<RunStatus, string> = {
    pending: '准备中',
    running: '运行中',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消',
    interrupted: '已中断',
  }
  return <span className={`badge badge-${status}`}>{labels[status]}</span>
}

export { ORDER }
