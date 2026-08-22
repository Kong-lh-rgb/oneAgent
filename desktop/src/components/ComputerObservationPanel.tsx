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
  if (failed) return <div className="computer-preview__missing">截图不可用</div>
  return (
    <img
      src={`${serverUrl}/computer/screenshots/${observationId}.png`}
      alt="最新目标应用截图"
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
  title = '目标预览',
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
        <p>暂无电脑观察结果</p>
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
          <p>{app?.name ?? '未知目标'} · {window?.title || '未命名窗口'}</p>
        </div>
        <time>{eventTime ? new Date(eventTime).toLocaleTimeString() : ''}</time>
      </div>

      <div className="computer-preview">
        {observation.id ? (
          <Screenshot observationId={observation.id} serverUrl={baseUrl} />
        ) : null}
        <dl className="computer-preview__meta">
          <div><dt>目标</dt><dd>{app?.name ?? '—'}</dd></div>
          <div><dt>窗口</dt><dd>{window?.title || '—'}</dd></div>
          <div><dt>Run</dt><dd className="mono">{runId?.slice(0, 8) ?? '—'}</dd></div>
          <div><dt>快照</dt><dd className="mono">{observation.id.slice(0, 8)}</dd></div>
        </dl>
      </div>

      <details className="observation-inspector">
        <summary>
          <span>观察详情</span>
          <small>
            {observation.element_stats?.observed ?? observation.elements.length} 个元素
            {' · '}{editableCount + actionableCount} 个可编辑/可操作
            {focused ? ` · 焦点：${focused.role.replaceAll('_', ' ')}` : ''}
          </small>
        </summary>
        <div className="observation-inspector__body">
          <div className="observation-inspector__summary mono">
            快照 {observation.id}
            {observation.truncated ? ' · 输出已截断' : ''}
          </div>
          <h4>窗口</h4>
          <div className="inspector-rows">
            {observation.windows.map((item) => (
              <div key={item.ref} className="inspector-row">
                <code>{item.ref}</code><span>{item.title || '未命名'}</span>
                <small>{item.bounds.width}×{item.bounds.height}</small>
              </div>
            ))}
          </div>
          <h4>界面元素（{elements.length}{observation.elements.length > MAX_ELEMENTS ? '+' : ''}）</h4>
          <div className="inspector-rows">
            {elements.map((element) => (
              <div key={element.ref} className="inspector-row">
                <code>{element.ref}</code>
                <span>{element.role || 'element'} · {element.title ?? element.value ?? '—'}</span>
                <small>
                  {element.focused ? '已聚焦 ' : ''}
                  {element.editable ? '可编辑 ' : ''}
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
