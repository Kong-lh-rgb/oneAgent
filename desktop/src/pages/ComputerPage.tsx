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
          <h2>{available ? 'Computer ready' : 'Computer unavailable'}</h2>
          <p>
            {available
              ? 'Vesta is not currently controlling an application.'
              : 'The macOS Computer runtime is not available.'}
          </p>
        </div>
      </section>
    )
  }

  return (
    <section className="computer-session">
      <div className="section-heading">
        <div><h2>Agent control</h2><p>Current Computer Session</p></div>
        <span className="computer-session__live">Live</span>
      </div>
      <dl className="computer-session__grid">
        <div><dt>Target</dt><dd>{context.target ?? 'Waiting for target'}</dd></div>
        <div><dt>Window</dt><dd>{context.window ?? '—'}</dd></div>
        <div className="computer-session__wide">
          <dt>Run</dt><dd>{runLabel ?? 'Active Computer work'}</dd>
        </div>
        <div><dt>Session</dt><dd>Active{acquiredAt ? ` · since ${new Date(acquiredAt).toLocaleTimeString()}` : ''}</dd></div>
        <div><dt>Mode</dt><dd>{context.executionMode ?? 'Targeted'}</dd></div>
        <div><dt>Last action</dt><dd>{context.lastAction ?? 'Preparing'}</dd></div>
        <div><dt>Verification</dt><dd>{context.verification ?? 'No pending verification'}</dd></div>
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
      title="Computer"
      subtitle="Live desktop control, target evidence, and runtime permissions."
      maxWidth={1120}
      actions={
        <span className={`page-live-status ${active ? 'active' : ''}`}>
          <span />{active ? 'Active' : statusQuery.data?.available ? 'Ready' : 'Unavailable'}
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
          <div className="section-heading"><div><h3>Recent actions</h3><p>Computer activity in this Run</p></div></div>
          {context.recentActions.length === 0 ? (
            <p className="empty-inline">No Computer actions yet.</p>
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
        <div className="section-heading"><div><h3>Runtime &amp; permissions</h3><p>Required macOS access</p></div></div>
        <ComputerStatusView
          status={statusQuery.data ?? null}
          loading={statusQuery.isLoading}
          onRequestPermission={(permission) => void requestPermission(permission)}
        />
      </section>
    </PageShell>
  )
}
