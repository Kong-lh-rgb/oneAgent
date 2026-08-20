/** Computer Observation 展示（可复用：ComputerPage / RunDetailPage）。只读。 */

import { useState } from 'react'

import type { ComputerObservation } from '../api/computer'

const MAX_ELEMENTS = 100

function field(label: string, value: string | null | undefined): React.JSX.Element {
  return (
    <div>
      <span className="text-muted">{label}：</span>
      <span>{value ?? '-'}</span>
    </div>
  )
}

function Screenshot({
  observationId,
  serverUrl,
}: {
  observationId: string
  serverUrl: string
}): React.JSX.Element {
  const [failed, setFailed] = useState(false)
  if (failed) {
    return <div className="text-dim">Screenshot unavailable</div>
  }
  return (
    <img
      src={`${serverUrl}/computer/screenshots/${observationId}.png`}
      alt="computer observation"
      style={{
        maxWidth: '100%',
        maxHeight: 360,
        border: '1px solid var(--border)',
        borderRadius: 6,
      }}
      onError={() => setFailed(true)}
    />
  )
}

export default function ComputerObservationPanel({
  observation,
  runId,
  eventTime,
  serverUrl,
  title = 'Computer Observation',
}: {
  observation: ComputerObservation | null
  runId: string | null
  eventTime: string | null
  serverUrl?: string
  title?: string
}): React.JSX.Element {
  const baseUrl = serverUrl ?? 'http://127.0.0.1:8000'

  if (!observation) {
    return (
      <div>
        <h3 style={{ fontSize: 14, margin: '0 0 8px' }}>{title}</h3>
        <div className="text-dim">暂无 Computer Observation</div>
      </div>
    )
  }

  const app = observation.active_app
  const window = observation.active_window
  const elements = observation.elements.slice(0, MAX_ELEMENTS)
  const hasMore = observation.elements.length > MAX_ELEMENTS

  return (
    <div>
      <h3 style={{ fontSize: 14, margin: '0 0 8px' }}>{title}</h3>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', gap: 12 }}>
        <div>
          {observation.id ? <Screenshot observationId={observation.id} serverUrl={baseUrl} /> : null}
        </div>
        <div>
          {field('Active App', app ? app.name : null)}
          {app?.bundle_id ? field('bundle id', app.bundle_id) : null}
          {field('Active Window', window ? window.title : null)}
          {field('Observation ID', observation.id)}
          {field('Run ID', runId)}
          {field('Event Time', eventTime)}
        </div>
      </div>

      <div style={{ marginTop: 12 }}>
        <h4 style={{ fontSize: 13, margin: '0 0 6px' }}>Windows</h4>
        {observation.windows.length === 0 ? (
          <div className="text-dim">-</div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>ref</th>
                <th>title</th>
                <th>bounds</th>
              </tr>
            </thead>
            <tbody>
              {observation.windows.map((w) => (
                <tr key={w.ref}>
                  <td className="text-dim">{w.ref}</td>
                  <td>{w.title || '-'}</td>
                  <td className="text-dim">
                    {w.bounds ? `${w.bounds.x},${w.bounds.y} ${w.bounds.width}x${w.bounds.height}` : '-'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div style={{ marginTop: 12 }}>
        <h4 style={{ fontSize: 13, margin: '0 0 6px' }}>
          Elements ({Math.min(elements.length, MAX_ELEMENTS)}
          {hasMore ? '+' : ''})
        </h4>
        {elements.length === 0 ? (
          <div className="text-dim">-</div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>ref</th>
                <th>role</th>
                <th>title/value</th>
                <th>focused</th>
                <th>enabled</th>
                <th>actions</th>
              </tr>
            </thead>
            <tbody>
              {elements.map((el) => (
                <tr key={el.ref}>
                  <td className="text-dim">{el.ref}</td>
                  <td>{el.role || '-'}</td>
                  <td>{el.title ?? el.value ?? '-'}</td>
                  <td>{el.focused ? 'yes' : ''}</td>
                  <td>{el.enabled ? '' : 'no'}</td>
                  <td className="text-dim">{(el.actions ?? []).join(', ') || '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
