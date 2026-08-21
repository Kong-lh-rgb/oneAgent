/// <reference types="vite/client" />

interface VestaDesktopApi {
  platform: string
  versions: {
    electron: string
    node: string
    chrome: string
  }
  openExternal: (url: string) => Promise<boolean>
  notify: (notification: {
    title: string
    body: string
    kind: 'approval' | 'run' | 'artifact'
  }) => void
  /** 独立浮动审批小窗的显隐（只有 Electron Main 能 show/hide 窗口）。 */
  setApprovalVisible: (visible: boolean) => void
}

interface Window {
  vesta?: VestaDesktopApi
}
