/// <reference types="vite/client" />

interface OneAgentDesktopApi {
  platform: string
  versions: {
    electron: string
    node: string
    chrome: string
  }
}

interface Window {
  oneagent?: OneAgentDesktopApi
}
