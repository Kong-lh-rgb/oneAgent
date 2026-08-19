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
  return <span className={`badge badge-${status}`}>{status}</span>
}

export { ORDER }
