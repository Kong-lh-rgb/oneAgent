/** 全局 Toast：轻量状态提示（成功 / 失败 / 信息），替代各处 inline notice。 */

import { create } from 'zustand'

export type ToastTone = 'success' | 'error' | 'info'

export interface ToastItem {
  id: string
  tone: ToastTone
  message: string
}

interface ToastsState {
  toasts: ToastItem[]
  push: (tone: ToastTone, message: string) => void
  dismiss: (id: string) => void
}

let nextId = 0
const AUTO_DISMISS_MS = 4000

export const useToastsStore = create<ToastsState>((set, get) => ({
  toasts: [],
  push: (tone, message) => {
    const id = `toast-${++nextId}`
    set((state) => ({ toasts: [...state.toasts, { id, tone, message }] }))
    globalThis.setTimeout(() => get().dismiss(id), AUTO_DISMISS_MS)
  },
  dismiss: (id) => {
    set((state) => ({ toasts: state.toasts.filter((item) => item.id !== id) }))
  },
}))

/** 便捷调用：toast.success('已批准') / toast.error('失败') / toast.info(...)。 */
export const toast = {
  success: (message: string): void =>
    useToastsStore.getState().push('success', message),
  error: (message: string): void =>
    useToastsStore.getState().push('error', message),
  info: (message: string): void =>
    useToastsStore.getState().push('info', message),
}
