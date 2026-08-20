/** Plan Mode 的独立确认界面，不复用权限审批视觉。 */

import type { ReactElement } from 'react'

import type { Task } from '../api/types'
import { Icon } from './Icon'

export default function PlanCard({
  task,
  busy = false,
  onAccept,
  onReject,
}: {
  task: Task
  busy?: boolean
  onAccept: (id: string) => void
  onReject: (id: string) => void
}): ReactElement {
  return (
    <section className="plan-card" data-testid="plan-card">
      <div className="plan-card__eyebrow">
        <Icon name="runs" size={14} />
        Plan
      </div>
      <h2>{task.title}</h2>
      {task.goal ? <p className="plan-card__goal">{task.goal}</p> : null}
      <ol className="plan-card__steps">
        {task.steps.map((step, index) => (
          <li key={step.id ?? index}>
            <span>{index + 1}</span>
            <div>{step.title}</div>
          </li>
        ))}
      </ol>
      <div className="plan-card__actions">
        <button
          type="button"
          className="btn btn-primary"
          disabled={busy}
          onClick={() => onAccept(task.id)}
        >
          Accept plan
        </button>
        <button
          type="button"
          className="btn btn-text-danger"
          disabled={busy}
          onClick={() => onReject(task.id)}
        >
          Reject
        </button>
      </div>
    </section>
  )
}
