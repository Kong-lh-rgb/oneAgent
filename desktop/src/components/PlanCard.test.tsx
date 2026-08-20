/** PlanCard：步骤、目标和独立确认动作测试。 */

import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import type { Task } from '../api/types'
import PlanCard from './PlanCard'

const task: Task = {
  id: 'task-1',
  title: 'Refactor Desktop UI',
  description: null,
  goal: 'Create a focused agent workspace.',
  status: 'pending',
  priority: 'normal',
  constraints: [],
  state: [],
  key_facts: [],
  steps: [
    { id: 'step-1', title: 'Inspect the shell', status: 'done', note: null },
    { id: 'step-2', title: 'Build the chat workspace', status: 'todo', note: null },
  ],
  owner_conversation_id: 'conv-1',
  run_ids: [],
  created_at: '2026-08-20T00:00:00+00:00',
  updated_at: '2026-08-20T00:00:00+00:00',
  completed_at: null,
  revision: 1,
}

describe('PlanCard', () => {
  it('按序展示计划并与权限审批保持独立视觉', () => {
    const html = renderToStaticMarkup(
      <PlanCard task={task} onAccept={() => {}} onReject={() => {}} />,
    )
    expect(html).toContain('Refactor Desktop UI')
    expect(html).toContain('Create a focused agent workspace.')
    expect(html).toContain('Inspect the shell')
    expect(html).toContain('Build the chat workspace')
    expect(html).toContain('Accept plan')
    expect(html).toContain('Reject')
    expect(html).not.toContain('Approval required')
  })
})
