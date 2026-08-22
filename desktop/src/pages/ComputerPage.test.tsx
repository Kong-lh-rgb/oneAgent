/** Computer Workspace：Active / Idle 产品状态测试。 */

import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import type { ComputerContextVM } from '../agent/turnPresentation'
import { ComputerSessionOverview } from './ComputerPage'

const context: ComputerContextVM = {
  target: 'TextEdit',
  window: 'Untitled',
  lastAction: 'Typed “ Vesta”',
  verification: 'Verified',
  executionMode: 'background ax',
  recentActions: [],
}

describe('ComputerSessionOverview', () => {
  it('Active Session 展示目标、Run、动作与验证', () => {
    const html = renderToStaticMarkup(
      <ComputerSessionOverview
        active
        available
        context={context}
        runLabel="Append text in TextEdit"
        acquiredAt="2026-08-21T10:00:00+08:00"
      />,
    )
    expect(html).toContain('Agent control')
    expect(html).toContain('TextEdit')
    expect(html).toContain('Untitled')
    expect(html).toContain('Append text in TextEdit')
    expect(html).toContain('Typed “ Vesta”')
    expect(html).toContain('Verified')
  })

  it('空闲时只显示克制的 ready 状态', () => {
    const html = renderToStaticMarkup(
      <ComputerSessionOverview
        active={false}
        available
        context={{ ...context, target: null }}
        runLabel={null}
        acquiredAt={null}
      />,
    )
    expect(html).toContain('Computer ready')
    expect(html).toContain('not currently controlling')
    expect(html).not.toContain('Agent control')
  })
})
