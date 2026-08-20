/** ComputerObservationPanel 渲染测试（renderToStaticMarkup，无 DOM）。 */

import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import type { ComputerObservation } from '../api/computer'
import ComputerObservationPanel from './ComputerObservationPanel'

const obsId = 'a'.repeat(32)

const observation: ComputerObservation = {
  id: obsId,
  created_at: null,
  active_app: { name: 'TextEdit', bundle_id: 'com.apple.TextEdit', pid: 100 },
  active_window: {
    ref: 'w1',
    title: 'Untitled',
    bounds: { x: 0, y: 0, width: 800, height: 600 },
  },
  windows: [
    { ref: 'w1', title: 'Untitled', bounds: { x: 0, y: 0, width: 800, height: 600 } },
  ],
  elements: [
    {
      ref: 'e1',
      role: 'text_area',
      title: null,
      value: 'hi',
      enabled: true,
      focused: true,
      bounds: null,
      actions: [],
    },
    {
      ref: 'e2',
      role: 'button',
      title: 'Save',
      value: null,
      enabled: true,
      focused: false,
      bounds: { x: 10, y: 10, width: 80, height: 30 },
      actions: ['press'],
    },
  ],
  // 本地绝对路径，绝不能被当作 img src。
  screenshot_ref: '/Users/me/.oneagent/computer/screenshots/xxxx.png',
}

describe('ComputerObservationPanel', () => {
  it('渲染 active app / window / run id / elements', () => {
    const html = renderToStaticMarkup(
      <ComputerObservationPanel
        observation={observation}
        runId="run-1"
        eventTime="2026-08-20T00:00:00+00:00"
        serverUrl="http://127.0.0.1:8000"
      />,
    )
    expect(html).toContain('TextEdit')
    expect(html).toContain('Untitled')
    expect(html).toContain('run-1')
    expect(html).toContain('e1')
    expect(html).toContain('e2')
    expect(html).toContain('Save')
    expect(html).toContain('press')
  })

  it('screenshot src 用 observation.id 构造，不用 screenshot_ref', () => {
    const html = renderToStaticMarkup(
      <ComputerObservationPanel
        observation={observation}
        runId={null}
        eventTime={null}
        serverUrl="http://127.0.0.1:8000"
      />,
    )
    expect(html).toContain(
      `src="http://127.0.0.1:8000/computer/screenshots/${obsId}.png"`,
    )
    expect(html).not.toContain('/Users/me/.oneagent')
    expect(html).not.toContain('file://')
  })

  it('空 elements / windows 正常显示占位', () => {
    const html = renderToStaticMarkup(
      <ComputerObservationPanel
        observation={{ ...observation, elements: [], windows: [] }}
        runId={null}
        eventTime={null}
        serverUrl="http://127.0.0.1:8000"
      />,
    )
    expect(html).toContain('Elements (0)')
    expect(html).toContain('Windows')
  })

  it('无 observation 显示空状态', () => {
    const html = renderToStaticMarkup(
      <ComputerObservationPanel
        observation={null}
        runId={null}
        eventTime={null}
        serverUrl="http://127.0.0.1:8000"
      />,
    )
    expect(html).toContain('暂无 Computer Observation')
  })
})
