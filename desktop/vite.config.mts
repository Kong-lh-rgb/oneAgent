import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Renderer 开发服务器；Electron main 通过 VITE_DEV_SERVER_URL 加载它。
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
