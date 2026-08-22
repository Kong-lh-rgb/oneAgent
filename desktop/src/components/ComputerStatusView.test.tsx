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
    expect(html).toContain('可用')
    expect(html).toContain('辅助功能')
    expect(html).toContain('已授权')
    expect(html).toContain('屏幕录制')
    expect(html).toContain('需要授权')
    expect(html).toContain('空闲')
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
    expect(html).toContain('不可用')
    expect(html).toContain('未找到原生 helper')
    expect(html).toContain('未知')
  })

  it('缺权限且有 handler 时显示 Request 按钮', () => {
    const html = renderToStaticMarkup(
      <ComputerStatusView status={status} onRequestPermission={() => {}} />,
    )
    expect(html).toContain('请求权限')
  })

  it('无 handler 时不显示 Request 按钮', () => {
    const html = renderToStaticMarkup(<ComputerStatusView status={status} />)
    expect(html).not.toContain('Request')
  })
})
