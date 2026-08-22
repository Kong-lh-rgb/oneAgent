/** ChatSidePanel：聊天界面右侧毛玻璃半透明浮窗（由 ChatPage 按点击顺序渲染）。 */

import type { ReactElement } from 'react'

export default function ChatSidePanel(): ReactElement {
  return (
    <aside className="chat-side-panel" aria-label="Side panel">
      <header className="chat-side-panel__header">
        <strong>侧栏</strong>
      </header>
      <div className="chat-side-panel__body">
        <p className="chat-side-panel__empty">面板内容待添加</p>
      </div>
    </aside>
  )
}
