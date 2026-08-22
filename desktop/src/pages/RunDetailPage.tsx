import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { listArtifacts } from '../api/artifacts'
import { SERVER_URL } from '../api/config'
import { getLatestComputerObservation } from '../api/computer'
import { cancelRun, getRun, getRunTrace, recoverRun } from '../api/runs'
import { buildTurnView, formatDuration, formatTokens, humanizeRunError } from '../agent/turnPresentation'
import ArtifactList from '../components/ArtifactList'
import ComputerObservationPanel from '../components/ComputerObservationPanel'
import ContextInspector from '../components/ContextInspector'
import { ConfirmDialog } from '../components/ConfirmDialog'
import ExecutionTrace from '../components/ExecutionTrace'
import { Icon } from '../components/Icon'
import { ErrorState, LoadingState } from '../components/PageStates'
import { PageShell } from '../components/PageShell'
import { ActivityItems } from '../components/RunActivity'
import RunBadge from '../components/RunBadge'
import UsageInspector from '../components/UsageInspector'
import { toast } from '../stores/toasts'

export default function RunDetailPage({
  runId,
  onBack,
  onOpenConversation,
}: {
  runId: string
  onBack: () => void
  onOpenConversation: (conversationId: string) => void
}): React.JSX.Element {
  const queryClient = useQueryClient()
  const [confirmCancel, setConfirmCancel] = useState(false)
  const runQuery = useQuery({ queryKey: ['run', runId], queryFn: () => getRun(runId), refetchInterval: 3000 })
  const traceQuery = useQuery({ queryKey: ['run-trace', runId], queryFn: () => getRunTrace(runId), refetchInterval: 3000 })
  const computerQuery = useQuery({ queryKey: ['computer-observation', runId], queryFn: () => getLatestComputerObservation(runId), retry: false })
  const artifactsQuery = useQuery({ queryKey: ['artifacts', 'run', runId], queryFn: () => listArtifacts({ runId, limit: 100 }) })
  const run = runQuery.data
  const events = traceQuery.data?.events ?? []
  const turn = buildTurnView(events)
  const error = run && ['failed', 'interrupted'].includes(run.status)
    ? humanizeRunError(run.stop_reason, run.error)
    : null

  const cancel = async (): Promise<void> => {
    try {
      await cancelRun(runId)
      toast.info('Run cancelled')
      void queryClient.invalidateQueries({ queryKey: ['run', runId] })
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setConfirmCancel(false)
    }
  }
  const recover = async (): Promise<void> => {
    try {
      const next = await recoverRun(runId)
      toast.success(`Recovered as Run ${next.run.id.slice(0, 8)}`)
      void queryClient.invalidateQueries({ queryKey: ['runs'] })
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : String(cause))
    }
  }

  return (
    <PageShell
      title="Run details"
      subtitle={`Execution ${runId.slice(0, 8)}`}
      maxWidth={1040}
      actions={
        <div className="page-actions">
          <button className="btn btn-sm" onClick={onBack}><Icon name="panelOpen" size={14} /> History</button>
          {run ? <RunBadge status={run.status} /> : null}
          {run?.status === 'running' ? <button className="btn btn-danger btn-sm" onClick={() => setConfirmCancel(true)}>Stop</button> : null}
          {run?.status === 'interrupted' ? <button className="btn btn-primary btn-sm" onClick={() => void recover()}>Recover</button> : null}
        </div>
      }
    >
      {runQuery.isPending || traceQuery.isPending ? <LoadingState label="Loading run…" />
        : runQuery.isError || traceQuery.isError ? <ErrorState message={String(runQuery.error ?? traceQuery.error)} />
          : run ? (
            <div className="run-detail">
              <section className="run-summary">
                <div className="section-heading">
                  <div><h2>Summary</h2><p>{run.user_message || 'Untitled execution'}</p></div>
                  {run.conversation_id ? (
                    <button
                      type="button"
                      className="btn btn-sm"
                      onClick={() => onOpenConversation(run.conversation_id as string)}
                    >
                      <Icon name="chat" size={14} /> Open conversation
                    </button>
                  ) : null}
                </div>
                {error ? <div className="run-error"><strong>{error.title}</strong><p>{error.message}</p></div> : null}
                <dl className="stat-strip">
                  <div><dt>Status</dt><dd>{run.status}</dd></div>
                  <div><dt>Mode</dt><dd>{run.mode}</dd></div>
                  <div><dt>Steps</dt><dd>{turn.steps || traceQuery.data?.run.steps || 0}</dd></div>
                  <div><dt>Actions</dt><dd>{turn.toolCount}</dd></div>
                  <div><dt>Tokens</dt><dd>{turn.usage ? formatTokens(turn.usage.totalTokens) : formatTokens(traceQuery.data?.run.total_tokens ?? 0)}</dd></div>
                  <div><dt>Duration</dt><dd>{formatDuration(turn.durationMs) || '—'}</dd></div>
                </dl>
              </section>

              <section className="run-detail-section">
                <div className="section-heading"><div><h2>Usage</h2><p>Main Agent, Post-Run, cache, and Provider total</p></div></div>
                <UsageInspector summary={traceQuery.data?.usage} />
              </section>

              <section className="run-detail-section">
                <div className="section-heading"><div><h2>Execution</h2><p>Human-readable work timeline</p></div></div>
                <ActivityItems events={events} />
              </section>

              <section className="run-detail-section">
                <div className="section-heading"><div><h2>Context</h2><p>Per-step input and compaction</p></div></div>
                <ContextInspector events={events} />
              </section>

              <RunArtifactsSection artifacts={artifactsQuery.data ?? []} />

              {computerQuery.data?.observation ? (
                <section className="run-detail-section">
                  <ComputerObservationPanel
                    observation={computerQuery.data.observation}
                    runId={computerQuery.data.run_id}
                    eventTime={computerQuery.data.event_time}
                    serverUrl={SERVER_URL}
                    title="Computer activity"
                  />
                </section>
              ) : null}

              <section className="run-detail-section">
                <div className="section-heading"><div><h2>Trace</h2><p>Complete execution events grouped by model step</p></div></div>
                <dl className="technical-grid">
                  <div><dt>Run ID</dt><dd>{run.id}</dd></div>
                  <div><dt>Conversation</dt><dd>{run.conversation_id ?? '—'}</dd></div>
                  <div><dt>Provider</dt><dd>{traceQuery.data?.run.provider ?? '—'}</dd></div>
                  <div><dt>Model</dt><dd>{traceQuery.data?.run.model ?? '—'}</dd></div>
                  <div><dt>Stop reason</dt><dd>{run.stop_reason ?? '—'}</dd></div>
                  <div><dt>Raw error</dt><dd>{run.error ?? '—'}</dd></div>
                </dl>
                <ExecutionTrace events={events} />
              </section>
            </div>
          ) : null}
      <ConfirmDialog
        open={confirmCancel}
        title="Stop this Run?"
        message="Vesta will stop after the current cancellable operation."
        confirmLabel="Stop Run"
        onConfirm={() => void cancel()}
        onCancel={() => setConfirmCancel(false)}
      />
    </PageShell>
  )
}

export function RunArtifactsSection({
  artifacts,
}: {
  artifacts: Awaited<ReturnType<typeof listArtifacts>>
}): React.JSX.Element | null {
  if (artifacts.length === 0) return null
  return (
    <section className="run-detail-section">
      <div className="section-heading"><div><h2>Artifacts</h2><p>Delivered results from this Run</p></div></div>
      <ArtifactList artifacts={artifacts} compact />
    </section>
  )
}
