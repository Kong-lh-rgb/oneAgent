/** Design Foundation 基础组件渲染测试。 */

import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import { Badge, Button, EmptyState, SectionHeader, StatusDot } from './ui'

describe('StatusDot', () => {
  it('渲染状态 tone', () => {
    expect(
      renderToStaticMarkup(<StatusDot tone="ready" />),
    ).toContain('status-dot--ready')
    expect(
      renderToStaticMarkup(<StatusDot tone="offline" />),
    ).toContain('status-dot--offline')
    expect(
      renderToStaticMarkup(<StatusDot tone="running" />),
    ).toContain('status-dot--running')
    expect(
      renderToStaticMarkup(<StatusDot tone="failed" />),
    ).toContain('status-dot--failed')
  })
})

describe('Badge', () => {
  it('渲染 tone class 与内容', () => {
    const html = renderToStaticMarkup(<Badge tone="success">完成</Badge>)
    expect(html).toContain('badge--success')
    expect(html).toContain('完成')
  })
})

describe('Button', () => {
  it('变体与尺寸 class', () => {
    expect(
      renderToStaticMarkup(<Button variant="primary" size="sm" />),
    ).toContain('btn-primary btn-sm')
    expect(renderToStaticMarkup(<Button variant="danger" />)).toContain(
      'btn-danger',
    )
  })
})

describe('EmptyState', () => {
  it('渲染标题与提示', () => {
    const html = renderToStaticMarkup(<EmptyState title="暂无数据" hint="稍后再试" />)
    expect(html).toContain('暂无数据')
    expect(html).toContain('稍后再试')
    expect(html).toContain('empty-state')
  })
})

describe('SectionHeader', () => {
  it('渲染标题与 hint', () => {
    const html = renderToStaticMarkup(<SectionHeader title="Results" hint="3 个交付物" />)
    expect(html).toContain('Results')
    expect(html).toContain('3 个交付物')
  })
})
