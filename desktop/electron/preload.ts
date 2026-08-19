import { contextBridge } from 'electron'

// preload 只暴露真正需要的最小 Desktop API（V0 不搞整套 RPC）。
// Renderer 通过 HTTP / WebSocket 直接与 localhost Agent Server 通信。
const desktopApi = {
  platform: process.platform,
  versions: {
    electron: process.versions.electron,
    node: process.versions.node,
    chrome: process.versions.chrome,
  },
} as const

contextBridge.exposeInMainWorld('oneagent', desktopApi)

export type DesktopApi = typeof desktopApi
