/** ConfirmDialog 渲染测试（renderToStaticMarkup，无 DOM）。 */

import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import { ConfirmDialog } from './ConfirmDialog'

const baseProps = {
  title: '取消 Run',
  onConfirm: () => {},
  onCancel: () => {},
}

describe('ConfirmDialog', () => {
  it('renders nothing when closed', () => {
    const html = renderToStaticMarkup(
      <ConfirmDialog {...baseProps} open={false} />,
    )
    expect(html).toBe('')
  })

  it('renders title, message and buttons when open', () => {
    const html = renderToStaticMarkup(
      <ConfirmDialog
        {...baseProps}
        open
        message="确认要取消这次运行吗？"
        confirmLabel="确认取消"
        cancelLabel="再想想"
      />,
    )
    expect(html).toContain('dialog-overlay')
    expect(html).toContain('dialog__title')
    expect(html).toContain('取消 Run')
    expect(html).toContain('dialog__message')
    expect(html).toContain('确认要取消这次运行吗？')
    expect(html).toContain('>确认取消</button>')
    expect(html).toContain('>再想想</button>')
  })

  it('uses danger tone by default and primary when requested', () => {
    const danger = renderToStaticMarkup(
      <ConfirmDialog {...baseProps} open confirmLabel="删除" />,
    )
    expect(danger).toContain('btn-danger')

    const primary = renderToStaticMarkup(
      <ConfirmDialog
        {...baseProps}
        open
        tone="primary"
        confirmLabel="确定"
      />,
    )
    expect(primary).toContain('btn-primary')
  })

  it('disables buttons while busy', () => {
    const html = renderToStaticMarkup(
      <ConfirmDialog {...baseProps} open busy />,
    )
    expect(html).toContain('disabled')
  })
})
