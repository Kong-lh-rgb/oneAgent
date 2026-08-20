/** ComputerStatusView 渲染测试（renderToStaticMarkup，无 DOM）。 */

import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import type { ComputerStatus } from '../api/computer'
import ComputerStatusView from './ComputerStatusView'

const status: ComputerStatus = {
  enabled: true,
  available: true,
  platform: 'macos',
  runtime: 'macos',
  reason: null,
  helper_path: null,
  permissions: { accessibility: 'granted', screen_recording: 'required' },
  lease: { busy: false, owner_run_id: '', acquired_at: null, process_id: 1 },
}

describe('ComputerStatusView', () => {
  it('available 渲染状态与权限', () => {
    const html = renderToStaticMarkup(<ComputerStatusView status={status} />)
    expect(html).toContain('Available')
    expect(html).toContain('Accessibility')
    expect(html).toContain('Granted')
    expect(html).toContain('Screen Recording')
    expect(html).toContain('Required')
    expect(html).toContain('Free')
  })

  it('unavailable 渲染 reason', () => {
    const html = renderToStaticMarkup(
      <ComputerStatusView
        status={{
          ...status,
          available: false,
          reason: 'helper_not_found',
          permissions: { accessibility: 'unknown', screen_recording: 'unknown' },
          lease: null,
        }}
      />,
    )
    expect(html).toContain('Unavailable')
    expect(html).toContain('helper not found')
    expect(html).toContain('Unknown')
  })

  it('缺权限且有 handler 时显示 Request 按钮', () => {
    const html = renderToStaticMarkup(
      <ComputerStatusView status={status} onRequestPermission={() => {}} />,
    )
    expect(html).toContain('Request')
  })

  it('无 handler 时不显示 Request 按钮', () => {
    const html = renderToStaticMarkup(<ComputerStatusView status={status} />)
    expect(html).not.toContain('Request')
  })
})
