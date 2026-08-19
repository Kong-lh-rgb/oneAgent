import { useEventsStore } from '../stores/events'
import type { AgentEvent } from '../api/types'

function describe(event: AgentEvent): string {
  switch (event.type) {
    case 'agent_started':
      return 'Agent 开始执行'
    case 'model_started':
      return `第 ${event.step ?? '?'} 步：请求模型`
    case 'model_completed': {
      const count = event.message?.tool_calls?.length ?? 0
      return count > 0 ? `模型请求调用 ${count} 个工具` : '模型已返回回复'
    }
    case 'tool_started':
      return `工具：${event.tool_call?.name ?? '?'}`
    case 'tool_completed': {
      const status = event.tool_result?.success ? '成功' : '失败'
      return `工具 ${event.tool_result?.tool_name ?? '?'} ${status}`
    }
    case 'tool_approval_required':
      return `等待审批：${event.tool_call?.name ?? '?'}`
    case 'agent_completed':
      return 'Agent 执行完成'
    case 'agent_failed':
      return `Agent 执行失败：${event.stop_reason ?? 'unknown'}`
    case 'memory_reflection_started':
      return '整理长期记忆'
    default:
      return event.type
  }
}

export default function RunActivity({
  runId,
}: {
  runId: string | null
}): React.JSX.Element {
  const eventsByRun = useEventsStore((state) => state.eventsByRun)
  const runStatuses = useEventsStore((state) => state.runStatuses)
  const events = runId ? eventsByRun[runId] ?? [] : []
  const status = runId ? runStatuses[runId] : undefined

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ padding: '10px 12px', borderBottom: '1px solid var(--border)', fontSize: 13, fontWeight: 600 }}>
        Run 执行进度
        {status && <span className={`badge badge-${status}`} style={{ marginLeft: 8 }}>{status}</span>}
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: 8 }}>
        {events.length === 0 && <div className="empty">等待 Run 事件…</div>}
        {events.map((event) => (
          <div key={event.event_id} style={{ padding: '3px 6px', fontSize: 12.5, color: 'var(--text-dim)' }}>
            <span className="text-muted" style={{ marginRight: 6 }}>
              {event.sequence}
            </span>
            {event.tool_call && (
              <span style={{ color: 'var(--accent)', marginRight: 6 }}>🔧</span>
            )}
            {describe(event)}
          </div>
        ))}
      </div>
    </div>
  )
}
