/** PageShell 渲染测试（renderToStaticMarkup，无 DOM）。 */

import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import { PageShell } from './PageShell'

describe('PageShell', () => {
  it('renders title, subtitle, actions and children', () => {
    const html = renderToStaticMarkup(
      <PageShell
        title="Approvals"
        subtitle="工具审批"
        actions={<button type="button">New</button>}
      >
        <p>body content</p>
      </PageShell>,
    )
    expect(html).toContain('page-shell')
    expect(html).toContain('page-shell__header')
    expect(html).toContain('page-shell__title')
    expect(html).toContain('Approvals')
    expect(html).toContain('工具审批')
    expect(html).toContain('>New</button>')
    expect(html).toContain('body content')
    expect(html).toContain('page-shell__body')
  })

  it('omits subtitle and actions when not provided', () => {
    const html = renderToStaticMarkup(<PageShell title="Runs">x</PageShell>)
    expect(html).not.toContain('page-shell__subtitle')
    expect(html).not.toContain('page-shell__actions')
    expect(html).toContain('Runs')
  })

  it('applies custom maxWidth to the body', () => {
    const html = renderToStaticMarkup(
      <PageShell title="Artifacts" maxWidth={720}>
        x
      </PageShell>,
    )
    expect(html).toContain('max-width:720px')
  })
})
