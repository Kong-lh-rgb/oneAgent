/** Context Inspector：按模型步骤展示输入构成、压缩结果与成本解释。 */

import { useEffect, useMemo, useState } from 'react'

import type { AgentEvent } from '../api/types'
import {
  buildContextSteps,
  explainRunCost,
  type ContextStepVM,
} from '../agent/runAnalysis'
import { formatTokens } from '../agent/turnPresentation'
import { EmptyState } from './ui'

function TokenTransition({ before, after }: { before: number; after: number }): React.JSX.Element {
  return (
    <span className="context-token-transition mono">
      {formatTokens(before)}{before !== after ? ` → ${formatTokens(after)}` : ''}
    </span>
  )
}

function CompactionList({ step }: { step: ContextStepVM }): React.JSX.Element {
  const actions = [
    step.compactedToolResults > 0
      ? `${step.compactedToolResults} 个工具结果已压缩`
      : null,
    step.removedToolRounds > 0
      ? `${step.removedToolRounds} 个旧工具轮已移除`
      : null,
    step.summaryUpdated ? 'Conversation summary 已更新' : null,
  ].filter((item): item is string => item !== null)

  return (
    <section className="context-section">
      <div className="context-section__heading">
        <h3>Compaction</h3>
        <span className={`context-stage ${step.compactionStage === 'none' ? '' : 'active'}`}>
          {step.compactionStage === 'none' ? '未触发' : step.compactionStage.replaceAll('_', ' ')}
        </span>
      </div>
      {actions.length > 0 ? (
        <ul className="context-checks">
          {actions.map((action) => <li key={action}>✓ {action}</li>)}
        </ul>
      ) : (
        <p className="context-muted">本步骤不需要压缩。</p>
      )}
      {step.summaryError ? <p className="context-warning">摘要更新失败：{step.summaryError}</p> : null}
    </section>
  )
}

export default function ContextInspector({
  events,
}: {
  events: AgentEvent[]
}): React.JSX.Element {
  const steps = useMemo(() => buildContextSteps(events), [events])
  const [selectedStep, setSelectedStep] = useState<number | null>(null)
  const selected = steps.find((step) => step.step === selectedStep) ?? steps.at(-1)
  const reasons = useMemo(() => explainRunCost(events), [events])

  useEffect(() => {
    if (selectedStep === null && steps.length > 0) setSelectedStep(steps.at(-1)!.step)
    if (selectedStep !== null && !steps.some((step) => step.step === selectedStep)) {
      setSelectedStep(steps.at(-1)?.step ?? null)
    }
  }, [selectedStep, steps])

  if (!selected) {
    return <EmptyState title="暂无 Context 数据" hint="模型请求开始后会记录上下文构成。" />
  }

  return (
    <div className="context-inspector">
      <div className="context-step-picker" aria-label="Context model step">
        <span>Model step</span>
        <div>
          {steps.map((step) => (
            <button
              key={step.step}
              type="button"
              className={step.step === selected.step ? 'active' : ''}
              aria-pressed={step.step === selected.step}
              onClick={() => setSelectedStep(step.step)}
            >
              {step.step}
            </button>
          ))}
        </div>
      </div>

      <section className="context-section">
        <h3>Context</h3>
        <dl className="context-metrics">
          <div><dt>Input</dt><dd><TokenTransition before={selected.originalInputTokens} after={selected.preparedInputTokens} /></dd></div>
          <div><dt>Context window</dt><dd className="mono">{formatTokens(selected.contextWindow)}</dd></div>
          <div><dt>Working budget</dt><dd className="mono">{formatTokens(selected.workingInputBudget)}</dd></div>
          <div><dt>Model input limit</dt><dd className="mono">{formatTokens(selected.inputBudget)}</dd></div>
          <div><dt>Trigger / target</dt><dd className="mono">{formatTokens(selected.triggerTokens)} / {formatTokens(selected.targetTokens)}</dd></div>
          <div><dt>Window usage</dt><dd className="mono">{(selected.windowUsageRatio * 100).toFixed(1)}%</dd></div>
          <div><dt>Budget usage</dt><dd className="mono">{(selected.budgetUsageRatio * 100).toFixed(1)}%</dd></div>
          <div><dt>Tool schemas</dt><dd className="mono">{formatTokens(selected.toolSchemaTokens)}</dd></div>
          <div><dt>Tool results</dt><dd><TokenTransition before={selected.toolResultTokensBefore} after={selected.toolResultTokensAfter} /></dd></div>
          <div><dt>Messages</dt><dd><TokenTransition before={selected.messageTokensBefore} after={selected.messageTokensAfter} /></dd></div>
          <div><dt>Skills</dt><dd className="mono">{formatTokens(selected.skillTokens)}</dd></div>
        </dl>
        <progress
          className="context-window-progress"
          max={Math.max(1, selected.contextWindow)}
          value={Math.min(selected.preparedInputTokens, Math.max(1, selected.contextWindow))}
          aria-label="Context window usage"
        />
      </section>

      <section className="context-section">
        <h3>Context breakdown</h3>
        <div className="context-breakdown">
          {selected.breakdown.map((item) => (
            <div key={item.key} className="context-breakdown__row">
              <span>{item.label}</span>
              <progress max={1} value={item.ratio} aria-label={`${item.label} ratio`} />
              <strong className="mono">{formatTokens(item.tokens)}</strong>
              <small className="mono">{(item.ratio * 100).toFixed(0)}%</small>
            </div>
          ))}
        </div>
        <p className="context-note">Memory、Task 与系统消息当前没有独立计数字段，统一包含在 Messages &amp; injected。</p>
      </section>

      <CompactionList step={selected} />

      <section className="context-section">
        <h3>为什么这轮可能更贵？</h3>
        {reasons.length > 0 ? (
          <ul className="context-reasons">
            {reasons.map((reason) => <li key={reason}>{reason}</li>)}
          </ul>
        ) : (
          <p className="context-muted">当前没有明显的上下文成本信号。</p>
        )}
      </section>
    </div>
  )
}
