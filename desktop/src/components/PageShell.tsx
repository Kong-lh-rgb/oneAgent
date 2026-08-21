/** 统一页面外壳：标题区（标题/副标题/操作）+ 可滚动内容区。

所有页面都走这个外壳，替代各自手写的 `<div style={{padding:16}}>` + `<h2>`，
保证信息层级与视觉一致。
*/

import type { ReactElement, ReactNode } from 'react'

export interface PageShellProps {
  title: string
  subtitle?: string
  /** 页面级操作（如“新建”按钮），渲染在标题右侧。 */
  actions?: ReactNode
  children?: ReactNode
  /** 内容区最大宽度（px）；不设则铺满。 */
  maxWidth?: number
}

export function PageShell({
  title,
  subtitle,
  actions,
  children,
  maxWidth = 960,
}: PageShellProps): ReactElement {
  return (
    <div className="page-shell">
      <header className="page-shell__header">
        <div className="page-shell__heading">
          <h1 className="page-shell__title">{title}</h1>
          {subtitle ? <p className="page-shell__subtitle">{subtitle}</p> : null}
        </div>
        {actions ? <div className="page-shell__actions">{actions}</div> : null}
      </header>
      <div className="page-shell__body" style={maxWidth ? { maxWidth } : undefined}>
        {children}
      </div>
    </div>
  )
}
