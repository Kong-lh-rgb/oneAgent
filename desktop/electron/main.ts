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
const APPROVAL_WIDTH = 400
const APPROVAL_MIN_HEIGHT = 220
const APPROVAL_MAX_HEIGHT = 560
const APPROVAL_TOP_MARGIN = 28
const APPROVAL_RIGHT_MARGIN = 20

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
    width: APPROVAL_WIDTH,
    height: APPROVAL_MIN_HEIGHT,
    show: false,
    frame: false,
    // 审批卡片可以接收鼠标点击，但不能成为 macOS key window。
    // 否则点击 Allow 会激活 Electron，app.activate 随即把主窗口置前，
    // 并与 Computer Runtime 恢复目标 App 的动作产生焦点竞态。
    focusable: false,
    acceptFirstMouse: true,
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

  // 跨 macOS Space 可见：用户切到其它 Space 操作 App 时审批仍能出现。
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true })

  if (!app.isPackaged) {
    void win.loadURL(`${DEV_SERVER_URL}/approval.html`)
  } else {
    // 从 dist-electron/electron 回到桌面根目录再进入 Vite 产物。
    void win.loadFile(path.join(__dirname, '..', '..', 'dist', 'approval.html'))
  }
  win.webContents.on('did-finish-load', () => {
    console.log('[approval] floating window loaded')
  })

  // 误触发关闭（如 Cmd+W）只隐藏不销毁：pending 审批不会丢、可重新出现。
  win.on('close', (event) => {
    if (process.platform === 'darwin' && !isQuitting) {
      event.preventDefault()
      win.hide()
    }
  })
  win.on('closed', () => {
    if (approvalWindow === win) approvalWindow = null
  })
}

/** 把浮窗放到「当前鼠标所在显示器」的右上角（避开系统通知区）。 */
function positionApprovalWindow(): void {
  if (approvalWindow === null || approvalWindow.isDestroyed()) return
  const cursor = screen.getCursorScreenPoint()
  const display = screen.getDisplayNearestPoint(cursor)
  const workArea = display.workArea
  const [width, height] = approvalWindow.getSize()
  approvalWindow.setPosition(
    workArea.x + workArea.width - width - APPROVAL_RIGHT_MARGIN,
    workArea.y + APPROVAL_TOP_MARGIN,
  )
}

/** 显示/隐藏浮动审批小窗（由浮窗 Renderer 触发）。 */
function setApprovalVisible(visible: boolean): void {
  if (!visible) {
    if (approvalWindow !== null && !approvalWindow.isDestroyed()) {
      approvalWindow.hide()
      console.log('[approval] hide')
    }
    return
  }
  createApprovalWindow()
  if (approvalWindow === null || approvalWindow.isDestroyed()) return
  positionApprovalWindow()
  // showInactive + focusable:false：只显示审批层，目标 App 始终保持前台。
  approvalWindow.showInactive()
  console.log('[approval] show')
}

function createWindow(): void {
  const win = new BrowserWindow({
    width: 1520,
    height: 860,
    minWidth: 1100,
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

ipcMain.on('vesta:approval-set-size', (_event, height: unknown) => {
  if (approvalWindow === null || approvalWindow.isDestroyed()) return
  if (typeof height !== 'number' || !Number.isFinite(height)) return
  const clamped = Math.min(
    APPROVAL_MAX_HEIGHT,
    Math.max(APPROVAL_MIN_HEIGHT, Math.round(height)),
  )
  const [width] = approvalWindow.getSize()
  approvalWindow.setContentSize(width, clamped)
})

void app.whenReady().then(() => {
  createWindow()
  // 浮窗常驻（隐藏），保持 WS 订阅，审批到来时立即弹出。
  createApprovalWindow()
  app.on('activate', () => {
    // 点击审批浮窗可能激活 Electron；此时绝不能把主窗口带到前台。
    if (approvalWindow !== null && approvalWindow.isVisible()) return
    showMainWindow()
  })
})

app.on('before-quit', () => {
  isQuitting = true
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
