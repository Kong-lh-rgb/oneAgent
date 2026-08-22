/** Computer Preview + 按需展开的结构化 Observation inspector。 */

import { useState } from 'react'

import type { ComputerObservation } from '../api/computer'

const MAX_ELEMENTS = 100

function Screenshot({
  observationId,
  serverUrl,
}: {
  observationId: string
  serverUrl: string
}): React.JSX.Element {
  const [failed, setFailed] = useState(false)
  if (failed) return <div className="computer-preview__missing">Screenshot unavailable</div>
  return (
    <img
      src={`${serverUrl}/computer/screenshots/${observationId}.png`}
      alt="Latest target application"
      className="computer-preview__image"
      onError={() => setFailed(true)}
    />
  )
}

export default function ComputerObservationPanel({
  observation,
  runId,
  eventTime,
  serverUrl,
  title = 'Target preview',
}: {
  observation: ComputerObservation | null
  runId: string | null
  eventTime: string | null
  serverUrl?: string
  title?: string
}): React.JSX.Element {
  if (!observation) {
    return (
      <section className="computer-observation computer-observation--empty">
        <h3>{title}</h3>
        <p>暂无 Computer Observation</p>
      </section>
    )
  }

  const baseUrl = serverUrl ?? 'http://127.0.0.1:8000'
  const app = observation.target ?? observation.active_app
  const window = observation.active_window
  const elements = observation.elements.slice(0, MAX_ELEMENTS)
  const editableCount = observation.element_stats?.editable_count
    ?? elements.filter((element) => element.editable).length
  const actionableCount = observation.element_stats?.actionable_count
    ?? elements.filter((element) => element.actions.length > 0).length
  const focused = elements.find(
    (element) => element.ref === observation.focused_element_ref,
  ) ?? elements.find((element) => element.focused)

  return (
    <section className="computer-observation">
      <div className="section-heading">
        <div>
          <h3>{title}</h3>
          <p>{app?.name ?? 'Unknown target'} · {window?.title || 'Untitled window'}</p>
        </div>
        <time>{eventTime ? new Date(eventTime).toLocaleTimeString() : ''}</time>
      </div>

      <div className="computer-preview">
        {observation.id ? (
          <Screenshot observationId={observation.id} serverUrl={baseUrl} />
        ) : null}
        <dl className="computer-preview__meta">
          <div><dt>Target</dt><dd>{app?.name ?? '—'}</dd></div>
          <div><dt>Window</dt><dd>{window?.title || '—'}</dd></div>
          <div><dt>Run</dt><dd className="mono">{runId?.slice(0, 8) ?? '—'}</dd></div>
          <div><dt>Snapshot</dt><dd className="mono">{observation.id.slice(0, 8)}</dd></div>
        </dl>
      </div>

      <details className="observation-inspector">
        <summary>
          <span>Observation</span>
          <small>
            {observation.element_stats?.observed ?? observation.elements.length} elements
            {' · '}{editableCount + actionableCount} editable/actionable
            {focused ? ` · focused: ${focused.role.replaceAll('_', ' ')}` : ''}
          </small>
        </summary>
        <div className="observation-inspector__body">
          <div className="observation-inspector__summary mono">
            snapshot {observation.id}
            {observation.truncated ? ' · output truncated' : ''}
          </div>
          <h4>Windows</h4>
          <div className="inspector-rows">
            {observation.windows.map((item) => (
              <div key={item.ref} className="inspector-row">
                <code>{item.ref}</code><span>{item.title || 'Untitled'}</span>
                <small>{item.bounds.width}×{item.bounds.height}</small>
              </div>
            ))}
          </div>
          <h4>Elements ({elements.length}{observation.elements.length > MAX_ELEMENTS ? '+' : ''})</h4>
          <div className="inspector-rows">
            {elements.map((element) => (
              <div key={element.ref} className="inspector-row">
                <code>{element.ref}</code>
                <span>{element.role || 'element'} · {element.title ?? element.value ?? '—'}</span>
                <small>
                  {element.focused ? 'focused ' : ''}
                  {element.editable ? 'editable ' : ''}
                  {element.actions.join(', ')}
                </small>
              </div>
            ))}
          </div>
        </div>
      </details>
    </section>
  )
}
