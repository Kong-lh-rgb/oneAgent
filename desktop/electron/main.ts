import {
  app,
  BrowserWindow,
  ipcMain,
  Notification,
  screen,
  shell,
} from 'electron'
import path from 'node:path'

const DEV_SERVER_URL = process.env.VITE_DEV_SERVER_URL ?? 'http://127.0.0.1:5173'
const NOTIFICATION_KINDS = new Set(['approval', 'run', 'artifact'])
const MAX_NOTIFICATION_TITLE = 100
const MAX_NOTIFICATION_BODY = 240

let mainWindow: BrowserWindow | null = null
let approvalWindow: BrowserWindow | null = null
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

/** 独立浮动审批小窗：无边框、置顶、不抢焦点（showInactive），空闲时隐藏。 */
function createApprovalWindow(): void {
  if (approvalWindow !== null && !approvalWindow.isDestroyed()) return
  const win = new BrowserWindow({
    width: 400,
    height: 480,
    show: false,
    frame: false,
    resizable: false,
    movable: true,
    alwaysOnTop: true,
    skipTaskbar: true,
    fullscreenable: false,
    minimizable: false,
    maximizable: false,
    title: 'Vesta Approval',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      // 安全边界：Renderer 不获得任意 Node 权限。
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      // 窗口隐藏期间保持 RPC / WebSocket 活跃，能实时收到审批事件。
      backgroundThrottling: false,
    },
  })
  approvalWindow = win

  if (!app.isPackaged) {
    void win.loadURL(`${DEV_SERVER_URL}/approval.html`)
  } else {
    // 从 dist-electron/electron 回到桌面根目录再进入 Vite 产物。
    void win.loadFile(path.join(__dirname, '..', '..', 'dist', 'approval.html'))
  }

  win.on('closed', () => {
    if (approvalWindow === win) approvalWindow = null
  })
}

/** 把浮窗放到主显示器右上角（避开系统通知区）。 */
function positionApprovalWindow(): void {
  if (approvalWindow === null || approvalWindow.isDestroyed()) return
  const workArea = screen.getPrimaryDisplay().workArea
  const [width, height] = approvalWindow.getSize()
  approvalWindow.setPosition(
    workArea.x + workArea.width - width - 20,
    workArea.y + 28,
  )
}

/** 显示/隐藏浮动审批小窗（由浮窗 Renderer 触发）。 */
function setApprovalVisible(visible: boolean): void {
  if (!visible) {
    if (approvalWindow !== null && !approvalWindow.isDestroyed()) {
      approvalWindow.hide()
    }
    return
  }
  createApprovalWindow()
  if (approvalWindow === null || approvalWindow.isDestroyed()) return
  positionApprovalWindow()
  // showInactive：浮在屏幕上但尽量不抢当前 App 的键盘焦点。
  approvalWindow.showInactive()
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

ipcMain.on('vesta:approval-set-visible', (_event, visible: unknown) => {
  if (visible !== true && visible !== false) return
  setApprovalVisible(visible)
})

void app.whenReady().then(() => {
  createWindow()
  // 浮窗常驻（隐藏），保持 WS 订阅，审批到来时立即弹出。
  createApprovalWindow()
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
