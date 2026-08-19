import { app, BrowserWindow, shell } from 'electron'
import path from 'node:path'

const DEV_SERVER_URL = process.env.VITE_DEV_SERVER_URL ?? 'http://127.0.0.1:5173'

function createWindow(): void {
  const win = new BrowserWindow({
    width: 1360,
    height: 860,
    minWidth: 980,
    minHeight: 640,
    title: 'OneAgent',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      // 安全边界：Renderer 不获得任意 Node 权限。
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  })

  if (!app.isPackaged) {
    void win.loadURL(DEV_SERVER_URL)
  } else {
    // 从 dist-electron/electron 回到桌面根目录再进入 Vite 产物。
    void win.loadFile(path.join(__dirname, '..', '..', 'dist', 'index.html'))
  }

  // 外部链接交给系统浏览器，不在 Electron 内打开。
  win.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url)
    return { action: 'deny' }
  })
}

void app.whenReady().then(() => {
  createWindow()
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
