/** 全局 Toast 视口：在 App 根挂载一次，展示所有 toast。 */

import type { ReactElement } from 'react'

import { useToastsStore } from '../stores/toasts'
import { Icon } from './Icon'

export function ToastViewport(): ReactElement {
  const toasts = useToastsStore((state) => state.toasts)
  const dismiss = useToastsStore((state) => state.dismiss)

  return (
    <div className="toast-viewport" aria-live="polite">
      {toasts.map((item) => (
        <div key={item.id} className={`toast toast--${item.tone}`} role="status">
          <span className="toast__message">{item.message}</span>
          <button
            type="button"
            className="toast__close"
            aria-label="Dismiss"
            onClick={() => dismiss(item.id)}
          >
            <Icon name="close" size={14} />
          </button>
        </div>
      ))}
    </div>
  )
}
