import {
  app,
  BrowserWindow,
  ipcMain,
  Notification,
  shell,
} from 'electron'
import path from 'node:path'

const DEV_SERVER_URL = process.env.VITE_DEV_SERVER_URL ?? 'http://127.0.0.1:5173'
const NOTIFICATION_KINDS = new Set(['approval', 'run', 'artifact'])
const MAX_NOTIFICATION_TITLE = 100
const MAX_NOTIFICATION_BODY = 240

let mainWindow: BrowserWindow | null = null
let isQuitting = false

interface NotificationPayload {
  title: string
  body: string
  kind: string
}

function showMainWindow(): void {
  if (mainWindow === null || mainWindow.isDestroyed()) {
    createWindow()
    return
  }
  mainWindow.show()
  mainWindow.focus()
}

function createWindow(): void {
  const win = new BrowserWindow({
    width: 1360,
    height: 860,
    minWidth: 980,
    minHeight: 640,
    title: 'Vesta',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      // 安全边界：Renderer 不获得任意 Node 权限。
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      // 窗口隐藏后仍保持 RPC / WebSocket 活跃，用于审批与完成通知。
      backgroundThrottling: false,
    },
  })
  mainWindow = win

  if (!app.isPackaged) {
    void win.loadURL(DEV_SERVER_URL)
  } else {
    // 从 dist-electron/electron 回到桌面根目录再进入 Vite 产物。
    void win.loadFile(path.join(__dirname, '..', '..', 'dist', 'index.html'))
  }

  // 外部链接交给系统浏览器，不在 Electron 内打开。
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (isHttpUrl(url)) void shell.openExternal(url)
    return { action: 'deny' }
  })

  // macOS 关闭窗口只隐藏，Renderer / WebSocket 保持活跃；显式 Quit 才退出。
  win.on('close', (event) => {
    if (process.platform === 'darwin' && !isQuitting) {
      event.preventDefault()
      win.hide()
    }
  })
  win.on('closed', () => {
    if (mainWindow === win) mainWindow = null
  })
}

function isHttpUrl(value: string): boolean {
  try {
    const protocol = new URL(value).protocol
    return protocol === 'http:' || protocol === 'https:'
  } catch {
    return false
  }
}

ipcMain.handle('vesta:open-external', async (_event, url: unknown) => {
  if (typeof url !== 'string' || !isHttpUrl(url)) return false
  await shell.openExternal(url)
  return true
})

ipcMain.on('vesta:notify', (_event, payload: unknown) => {
  if (!payload || typeof payload !== 'object' || !Notification.isSupported()) return
  const value = payload as Partial<NotificationPayload>
  if (
    typeof value.title !== 'string' ||
    typeof value.body !== 'string' ||
    typeof value.kind !== 'string' ||
    !NOTIFICATION_KINDS.has(value.kind)
  ) return

  const notification = new Notification({
    title: value.title.slice(0, MAX_NOTIFICATION_TITLE),
    body: value.body.slice(0, MAX_NOTIFICATION_BODY),
  })
  notification.on('click', showMainWindow)
  notification.show()
})

void app.whenReady().then(() => {
  createWindow()
  app.on('activate', () => {
    showMainWindow()
  })
})

app.on('before-quit', () => {
  isQuitting = true
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
