/** PageStates 渲染测试（renderToStaticMarkup，无 DOM）。 */

import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import { EmptyState, ErrorState, LoadingState } from './PageStates'

describe('LoadingState', () => {
  it('renders spinner and label', () => {
    const html = renderToStaticMarkup(<LoadingState label="加载中…" />)
    expect(html).toContain('page-state')
    expect(html).toContain('spinner')
    expect(html).toContain('加载中…')
  })
})

describe('EmptyState', () => {
  it('renders title, hint, icon and optional action', () => {
    const html = renderToStaticMarkup(
      <EmptyState
        title="没有待审批"
        hint="Agent 请求确认时会出现在这里。"
        icon="approvals"
        action={<button type="button">Retry</button>}
      />,
    )
    expect(html).toContain('page-state--empty')
    expect(html).toContain('page-state__title')
    expect(html).toContain('没有待审批')
    expect(html).toContain('page-state__hint')
    expect(html).toContain('Agent 请求确认时会出现在这里。')
    expect(html).toContain('>Retry</button>')
  })

  it('renders without hint or action', () => {
    const html = renderToStaticMarkup(<EmptyState title="空" />)
    expect(html).not.toContain('page-state__hint')
    expect(html).not.toContain('page-state__action')
  })
})

describe('ErrorState', () => {
  it('renders message with Retry button when onRetry provided', () => {
    const html = renderToStaticMarkup(
      <ErrorState message="加载失败" onRetry={() => {}} />,
    )
    expect(html).toContain('page-state--error')
    expect(html).toContain('加载失败')
    expect(html).toContain('>Retry</button>')
  })

  it('renders message without Retry button when onRetry omitted', () => {
    const html = renderToStaticMarkup(<ErrorState message="加载失败" />)
    expect(html).not.toContain('page-state__action')
  })
})
