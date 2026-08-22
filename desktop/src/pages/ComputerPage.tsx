/** Live Computer Workspace：Session、Target、Preview、动作与权限。 */

import { useQuery, useQueryClient } from '@tanstack/react-query'

import { SERVER_URL } from '../api/config'
import {
  getComputerStatus,
  getLatestComputerObservation,
  requestComputerPermission,
} from '../api/computer'
import { getRun } from '../api/runs'
import {
  buildComputerContext,
  type ComputerContextVM,
} from '../agent/turnPresentation'
import ComputerObservationPanel from '../components/ComputerObservationPanel'
import ComputerStatusView from '../components/ComputerStatusView'
import { PageShell } from '../components/PageShell'
import { Icon } from '../components/Icon'
import { useEventsStore } from '../stores/events'

export function ComputerSessionOverview({
  active,
  available,
  context,
  runLabel,
  acquiredAt,
}: {
  active: boolean
  available: boolean
  context: ComputerContextVM
  runLabel: string | null
  acquiredAt: string | null
}): React.JSX.Element {
  if (!active) {
    return (
      <section className="computer-ready">
        <Icon name="computer" size={20} />
        <div>
          <h2>{available ? '电脑操作已就绪' : '电脑操作不可用'}</h2>
          <p>
            {available
              ? 'Vesta 当前没有控制任何应用。'
              : 'macOS 电脑操作运行时当前不可用。'}
          </p>
        </div>
      </section>
    )
  }

  return (
    <section className="computer-session">
      <div className="section-heading">
        <div><h2>Agent 控制</h2><p>当前电脑操作会话</p></div>
        <span className="computer-session__live">实时</span>
      </div>
      <dl className="computer-session__grid">
        <div><dt>目标应用</dt><dd>{context.target ?? '等待确定目标'}</dd></div>
        <div><dt>窗口</dt><dd>{context.window ?? '—'}</dd></div>
        <div className="computer-session__wide">
          <dt>Run</dt><dd>{runLabel ?? '正在执行电脑操作'}</dd>
        </div>
        <div><dt>会话</dt><dd>活动中{acquiredAt ? ` · ${new Date(acquiredAt).toLocaleTimeString('zh-CN')} 开始` : ''}</dd></div>
        <div><dt>模式</dt><dd>{context.executionMode ?? '定向操作'}</dd></div>
        <div><dt>最近动作</dt><dd>{context.lastAction ?? '正在准备'}</dd></div>
        <div><dt>验证</dt><dd>{context.verification ?? '暂无待验证动作'}</dd></div>
      </dl>
    </section>
  )
}

export default function ComputerPage(): React.JSX.Element {
  const queryClient = useQueryClient()
  const eventsByRun = useEventsStore((state) => state.eventsByRun)
  const statusQuery = useQuery({
    queryKey: ['computer-status'],
    queryFn: getComputerStatus,
    refetchInterval: 2500,
    retry: false,
  })
  const observationQuery = useQuery({
    queryKey: ['computer-observation'],
    queryFn: () => getLatestComputerObservation(),
    refetchInterval: 2500,
    retry: false,
  })
  const activeRunId = statusQuery.data?.lease?.owner_run_id || null
  const runQuery = useQuery({
    queryKey: ['run', activeRunId],
    queryFn: () => getRun(activeRunId!),
    enabled: Boolean(activeRunId),
    refetchInterval: 3000,
  })
  const latest = observationQuery.data
  const context = buildComputerContext(
    activeRunId ? (eventsByRun[activeRunId] ?? []) : [],
    latest?.observation ?? null,
  )
  const active = Boolean(statusQuery.data?.lease?.busy && activeRunId)
  const acquiredAt = statusQuery.data?.lease?.acquired_at

  const requestPermission = async (
    permission: 'accessibility' | 'screen_recording',
  ): Promise<void> => {
    await requestComputerPermission(permission)
    void queryClient.invalidateQueries({ queryKey: ['computer-status'] })
  }

  return (
    <PageShell
      title="电脑"
      subtitle="查看桌面控制状态、目标证据与运行时权限。"
      maxWidth={1400}
      actions={
        <span className={`page-live-status ${active ? 'active' : ''}`}>
          <span />{active ? '控制中' : statusQuery.data?.available ? '已就绪' : '不可用'}
        </span>
      }
    >
      <ComputerSessionOverview
        active={active}
        available={Boolean(statusQuery.data?.available)}
        context={context}
        runLabel={runQuery.data?.user_message || activeRunId?.slice(0, 8) || null}
        acquiredAt={acquiredAt ?? null}
      />

      <div className="computer-workspace-grid">
        <ComputerObservationPanel
          observation={latest?.observation ?? null}
          runId={latest?.run_id ?? null}
          eventTime={latest?.event_time ?? null}
          serverUrl={SERVER_URL}
        />
        <aside className="computer-actions">
          <div className="section-heading"><div><h3>最近动作</h3><p>当前 Run 中的电脑操作</p></div></div>
          {context.recentActions.length === 0 ? (
            <p className="empty-inline">暂无电脑操作。</p>
          ) : (
            <ol>
              {context.recentActions.map((action) => (
                <li key={action.id} className={`computer-action computer-action--${action.state}`}>
                  <span>{action.state === 'done' ? '✓' : action.state === 'failed' ? '×' : '·'}</span>
                  <div><strong>{action.label}</strong>{action.verification ? <small>{action.verification}</small> : null}</div>
                </li>
              ))}
            </ol>
          )}
        </aside>
      </div>

      <section className="computer-permissions">
        <div className="section-heading"><div><h3>运行时与权限</h3><p>macOS 所需系统权限</p></div></div>
        <ComputerStatusView
          status={statusQuery.data ?? null}
          loading={statusQuery.isLoading}
          onRequestPermission={(permission) => void requestPermission(permission)}
        />
      </section>
    </PageShell>
  )
}
