import { beforeEach, describe, expect, it, vi } from 'vitest'

import { DesktopNotificationController } from './desktop'

describe('desktop notifications', () => {
  const handlers = new Map<string, (params: unknown) => void>()
  const client = {
    on: vi.fn((method: string, handler: (params: unknown) => void) => {
      handlers.set(method, handler)
      return () => handlers.delete(method)
    }),
  }
  const notify = vi.fn()
  let hidden = true

  beforeEach(() => {
    handlers.clear()
    notify.mockReset()
    client.on.mockClear()
    hidden = true
  })

  function start(): DesktopNotificationController {
    const controller = new DesktopNotificationController(
      client,
      { notify },
      () => hidden,
    )
    controller.start()
    return controller
  }

  it('approval.required 投递安全通知', () => {
    start()
    handlers.get('approval.required')?.({ approval: { id: 'approval-1' } })
    expect(notify).toHaveBeenCalledWith({
      title: 'Vesta needs your approval',
      body: 'A tool is waiting for your decision.',
      kind: 'approval',
    })
  })

  it('前台不通知，hidden 时仅通知 Run 终态', () => {
    start()
    hidden = false
    handlers.get('run.status')?.({ run_id: 'run-1', status: 'completed' })
    hidden = true
    handlers.get('run.status')?.({ run_id: 'run-1', status: 'running' })
    expect(notify).not.toHaveBeenCalled()
    handlers.get('run.status')?.({ run_id: 'run-1', status: 'completed' })
    expect(notify).toHaveBeenCalledOnce()
  })

  it('artifact.created 投递且重复事件只通知一次', () => {
    start()
    const event = { artifact: { id: 'artifact-1', title: 'Secret content' } }
    handlers.get('artifact.created')?.(event)
    handlers.get('artifact.created')?.(event)
    expect(notify).toHaveBeenCalledOnce()
    expect(JSON.stringify(notify.mock.calls[0])).not.toContain('Secret content')
  })
})
