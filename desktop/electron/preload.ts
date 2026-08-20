import { contextBridge, ipcRenderer } from 'electron'

export type DesktopNotificationKind = 'approval' | 'run' | 'artifact'

export interface DesktopNotification {
  title: string
  body: string
  kind: DesktopNotificationKind
}

// preload 只暴露真正需要的最小 Desktop API（V0 不搞整套 RPC）。
// Renderer 通过 HTTP / WebSocket 直接与 localhost Agent Server 通信。
const desktopApi = {
  platform: process.platform,
  versions: {
    electron: process.versions.electron,
    node: process.versions.node,
    chrome: process.versions.chrome,
  },
  openExternal: (url: string): Promise<boolean> =>
    ipcRenderer.invoke('oneagent:open-external', url) as Promise<boolean>,
  notify: (notification: DesktopNotification): void => {
    ipcRenderer.send('oneagent:notify', notification)
  },
} as const

contextBridge.exposeInMainWorld('oneagent', desktopApi)

export type DesktopApi = typeof desktopApi
