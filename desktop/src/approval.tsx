import React from 'react'
import ReactDOM from 'react-dom/client'

import ApprovalFloatingWindow from './components/ApprovalFloatingWindow'
import './index.css'

// 独立浮动审批小窗入口：由 Electron Main 在单独的 BrowserWindow 中加载
// （approval.html）。浮窗自带一条 WS /rpc 连接，直接订阅审批事件。
ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <ApprovalFloatingWindow />
  </React.StrictMode>,
)
