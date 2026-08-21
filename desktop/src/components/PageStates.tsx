/** 页面级状态：加载 / 空 / 错误（统一视觉，替代散落的 `.empty` 与文字）。 */

import type { ReactElement, ReactNode } from 'react'

import { Icon } from './Icon'
import type { IconName } from './Icon'
import { Button } from './ui'

export function LoadingState({ label = 'Loading…' }: { label?: string }): ReactElement {
  return (
    <div className="page-state">
      <span className="spinner" />
      <span className="page-state__label">{label}</span>
    </div>
  )
}

export function EmptyState({
  title,
  hint,
  icon = 'activity',
  action,
}: {
  title: string
  hint?: string
  icon?: IconName
  action?: ReactNode
}): ReactElement {
  return (
    <div className="page-state page-state--empty">
      <div className="page-state__icon">
        <Icon name={icon} size={22} />
      </div>
      <div className="page-state__title">{title}</div>
      {hint ? <div className="page-state__hint">{hint}</div> : null}
      {action ? <div className="page-state__action">{action}</div> : null}
    </div>
  )
}

export function ErrorState({
  message,
  hint,
  onRetry,
}: {
  message: string
  hint?: string
  onRetry?: () => void
}): ReactElement {
  return (
    <div className="page-state page-state--error">
      <div className="page-state__title">{message}</div>
      {hint ? <div className="page-state__hint">{hint}</div> : null}
      {onRetry ? (
        <div className="page-state__action">
          <Button variant="ghost" size="sm" onClick={onRetry}>
            Retry
          </Button>
        </div>
      ) : null}
    </div>
  )
}
